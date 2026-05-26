import streamlit as st
import pandas as pd
from pykrx import stock
import FinanceDataReader as fdr
from datetime import datetime, timedelta
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import os

# 1. KRX 로그인 정보 (보안 금고 연동)
os.environ['KRX_ID'] = st.secrets["KRX_ID"]
os.environ['KRX_PW'] = st.secrets["KRX_PW"]

# --- 페이지 설정 ---
st.set_page_config(layout="wide", page_title="나만의 종목 분석 엔진")
st.title("🖥️ 나만의 종목 분석 엔진")


# --- 종목 검색 로직 ---
@st.cache_data
def load_stock_list():
    df = fdr.StockListing('KRX')
    return df[['Code', 'Name']]


def get_real_code(user_input, stock_list):
    if user_input.isdigit() and len(user_input) == 6:
        if user_input in stock_list['Code'].values:
            return user_input
    else:
        result = stock_list[stock_list['Name'] == user_input]
        if not result.empty:
            return result.iloc[0]['Code']
    return None


df_stocks = load_stock_list()

# --- 1. 최상단 - 입력 및 핵심 타점 ---
st.markdown("### 1. 최상단 - 입력 및 핵심 타점 (진입/탈출가)")
col1, col2 = st.columns([1, 4])

with col1:
    user_input = st.text_input("종목명 또는 코드를 입력하세요", value="삼성전자")

    # 🌟 [수정됨]: 사용자가 보조지표(RSI, Williams %R) 기간 수치를 직접 입력하고 조절할 수 있는 위젯 추가
    rsi_period = st.number_input("RSI 기간 설정", min_value=2, max_value=100, value=14, step=1)
    will_period = st.number_input("Williams %R 기간 설정", min_value=2, max_value=100, value=14, step=1)

    analyze_btn = st.button("분석 실행")

end_date = datetime.now().strftime("%Y%m%d")
start_date = (datetime.now() - timedelta(days=180)).strftime("%Y%m%d")

stock_code = get_real_code(user_input, df_stocks)

if analyze_btn or stock_code:
    if stock_code:
        stock_name = df_stocks[df_stocks['Code'] == stock_code].iloc[0]['Name']
        try:
            with st.spinner('데이터를 정밀 분석 중입니다...'):
                # 1. 일봉 데이터 수집
                df_ohlcv = fdr.DataReader(stock_code, start_date, end_date)

                # --- 🛠️ 수정됨: 실제 보조 지표 계산 로직 투입 ---
                # RSI 계산 (동적 기간 적용)
                # 🌟 [수정됨]: 하드코딩된 14(com=13) 대신, 입력받은 rsi_period 수치를 반영하여 계산하도록 수정
                delta = df_ohlcv['Close'].diff()
                up = delta.clip(lower=0)
                down = -1 * delta.clip(upper=0)
                ema_up = up.ewm(com=rsi_period - 1, adjust=False).mean()
                ema_down = down.ewm(com=rsi_period - 1, adjust=False).mean()
                rs = ema_up / ema_down
                df_ohlcv['RSI'] = 100 - (100 / (1 + rs))

                # Williams %R 계산 (동적 기간 적용)
                # 🌟 [수정됨]: 하드코딩된 14 대신, 입력받은 will_period 수치를 반영하여 계산하도록 수정
                hh = df_ohlcv['High'].rolling(window=will_period).max()
                ll = df_ohlcv['Low'].rolling(window=will_period).min()
                df_ohlcv['Williams_R'] = (hh - df_ohlcv['Close']) / (hh - ll) * -100

                # 2. 수급 데이터 (pykrx)
                df_investor_raw = stock.get_market_trading_value_by_date(start_date, end_date, stock_code)

                # 🌟 [수정됨]: KRX 서버가 클라우드 IP를 봇으로 간주하고 "빈 데이터"를 주는 것을 감지하는 완벽 방어 로직 추가
                is_investor_data_available = df_investor_raw is not None and not df_investor_raw.empty
                is_naver_fallback = False  # 네이버 우회 수집 여부 플래그 초기화

                if is_investor_data_available:
                    # 데이터가 있을 때만 인덱스/컬럼 파싱 진행
                    if '날짜' in df_investor_raw.columns:
                        df_investor_raw = df_investor_raw.set_index('날짜')
                    elif '일자' in df_investor_raw.columns:
                        df_investor_raw = df_investor_raw.set_index('일자')
                    elif 'Date' in df_investor_raw.columns:
                        df_investor_raw = df_investor_raw.set_index('Date')

                    df_investor_raw.index = pd.to_datetime(df_investor_raw.index).strftime('%Y-%m-%d')

                    inst_cols = ['금융투자', '보험', '투신', '사모', '은행', '기타금융', '연기금']
                    available_inst_cols = [c for c in inst_cols if c in df_investor_raw.columns]
                    df_investor_raw['기관합계'] = df_investor_raw[available_inst_cols].sum(axis=1)

                    foreign_cols = [c for c in df_investor_raw.columns if '외국' in c and '기타' not in c]
                    retail_cols = [c for c in df_investor_raw.columns if '개인' in c]

                    cols_to_extract = []
                    if foreign_cols: cols_to_extract.append(foreign_cols[0])
                    cols_to_extract.append('기관합계')
                    if retail_cols: cols_to_extract.append(retail_cols[0])

                    df_investor = df_investor_raw[cols_to_extract].copy()

                    rename_dict = {}
                    if foreign_cols: rename_dict[foreign_cols[0]] = '외국인'
                    if retail_cols: rename_dict[retail_cols[0]] = '개인'
                    df_investor.rename(columns=rename_dict, inplace=True)
                    df_investor = df_investor / 100000000
                else:
                    # 🌟 [수정됨]: KRX 차단 시 빈 화면으로 두지 않고, 네이버 증권 우회 엔진을 가동하여 외국인/기관 수급량(주)을 실시간 크롤링하여 완벽 보완
                    try:
                        import requests
                        from io import StringIO

                        url = f"https://finance.naver.com/item/frgn.nhn?code={stock_code}"
                        headers = {
                            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
                        res = requests.get(url, headers=headers, timeout=5)
                        tables = pd.read_html(StringIO(res.text))

                        df_naver = None
                        for t in tables:
                            if '기관순매매량' in t.columns:
                                df_naver = t
                                break

                        if df_naver is not None and not df_naver.empty:
                            df_naver = df_naver.dropna(subset=['날짜'])
                            df_naver['날짜'] = pd.to_datetime(df_naver['날짜'], errors='coerce').dt.strftime('%Y-%m-%d')
                            df_naver = df_naver.dropna(subset=['날짜'])

                            df_naver.rename(columns={'외국인순매매량': '외국인', '기관순매매량': '기관합계'}, inplace=True)
                            df_naver = df_naver[['날짜', '외국인', '기관합계']]
                            df_naver = df_naver.set_index('날짜')

                            df_naver['외국인'] = pd.to_numeric(df_naver['외국인'], errors='coerce').fillna(0)
                            df_naver['기관합계'] = pd.to_numeric(df_naver['기관합계'], errors='coerce').fillna(0)

                            # 과거 데이터가 아래에 있으므로 시간 정배열(오름차순)로 뒤집기
                            df_investor = df_naver.sort_index(ascending=True)
                            is_naver_fallback = True
                        else:
                            df_investor = pd.DataFrame()
                    except Exception as e:
                        df_investor = pd.DataFrame()

                # fdr 일봉 데이터(Volume) 처리
                df_vol = df_ohlcv[['Volume']].copy()
                df_vol.index = pd.to_datetime(df_vol.index).strftime('%Y-%m-%d')
                df_vol['Vol_Change_Pct'] = df_vol['Volume'].pct_change() * 100

                # 수급 데이터가 없더라도 fdr의 '거래량' 데이터는 살아남도록 Left Join으로 강제 우회
                if not df_investor.empty:
                    df_merged = df_vol.join(df_investor, how='left').tail(10)
                else:
                    df_merged = df_vol.tail(10)

                # UI 표기를 위해 인덱스를 MM/DD 형식의 문자열로 변환
                df_merged.index = pd.to_datetime(df_merged.index).strftime('%m/%d')

                # 거래량 텍스트 생성
                df_merged['당일 거래량 (추이)'] = df_merged.apply(
                    lambda
                        x: f"{x['Volume'] / 1000:,.0f}K ({'+' if x['Vol_Change_Pct'] > 0 else ''}{x['Vol_Change_Pct']:.0f}%)" if pd.notnull(
                        x.get('Vol_Change_Pct')) else f"{x['Volume'] / 1000:,.0f}K",
                    axis=1
                )

                # 화면 UI용 최종 컬럼 순서 재배치 (데이터가 없으면 알아서 빼고 렌더링)
                final_cols = ['당일 거래량 (추이)']
                if '외국인' in df_merged.columns: final_cols.append('외국인')
                if '기관합계' in df_merged.columns: final_cols.append('기관합계')
                if '개인' in df_merged.columns: final_cols.append('개인')

                df_display = df_merged[final_cols]

                # 현재 주가 및 매물대(Volume Profile) 분석을 위한 데이터 처리
                current_price = df_ohlcv['Close'].iloc[-1]

                total_volume = df_ohlcv['Volume'].sum()
                # 가격을 20개 구간(Bin)으로 나누어 거래량 누적
                df_ohlcv['Price_Bin'] = pd.cut(df_ohlcv['Close'], bins=20)
                volume_profile = df_ohlcv.groupby('Price_Bin', observed=True)['Volume'].sum().reset_index()
                volume_profile['Vol_Pct'] = (volume_profile['Volume'] / total_volume) * 100

                # pd.IntervalIndex를 활용한 안전한 벡터화 추출 방식으로 수정
                intervals = pd.IntervalIndex(volume_profile['Price_Bin'])
                volume_profile['Bin_Center'] = intervals.mid.astype(float)
                volume_profile['Bin_Bottom'] = intervals.left.astype(float)
                volume_profile['Bin_Top'] = intervals.right.astype(float)

                # 상방(현재가 위)과 하방(현재가 아래) 매물대 분리
                upper_profile = volume_profile[volume_profile['Bin_Center'].astype(float) > current_price]
                lower_profile = volume_profile[volume_profile['Bin_Center'].astype(float) <= current_price]

                # 가장 거래량이 많은 상방 저항 매물대와 하방 지지 매물대 추출
                upper_res = upper_profile.loc[upper_profile['Volume'].idxmax()] if not upper_profile.empty else None
                lower_sup = lower_profile.loc[lower_profile['Volume'].idxmax()] if not lower_profile.empty else None

            with col2:
                change_percent = ((current_price - df_ohlcv['Close'].iloc[-2]) / df_ohlcv['Close'].iloc[-2]) * 100
                st.metric(label=f"현재가 ({stock_name})", value=f"{current_price:,.0f}원", delta=f"{change_percent:.2f}%")

                ma20 = df_ohlcv['Close'].rolling(window=20).mean().iloc[-1]
                atr = (df_ohlcv['High'] - df_ohlcv['Low']).rolling(window=14).mean().iloc[-1]

                entry_price = ma20 * 0.99
                target_price = entry_price + (atr * 1.5)
                stop_price = entry_price - (atr * 1.0)

                t1, t2, t3 = st.columns(3)
                with t1:
                    st.success(f"🎯 진입가격 (ENTRY)\n### {entry_price:,.0f}원")
                with t2:
                    st.info(f"📈 목표가격 (TARGET)\n### {target_price:,.0f}원")
                with t3:
                    st.error(f"📉 손절가격 (STOP)\n### {stop_price:,.0f}원")

            # --- 2. 차트 분석 영역 ---
            st.markdown("---")
            st.markdown("### 2. 차트 분석 영역 (1일봉 및 매물대)")

            # 서브타이틀 동적 적용
            fig = make_subplots(rows=4, cols=1, shared_xaxes=True,
                                vertical_spacing=0.03,
                                subplot_titles=(f'{stock_name} 일봉', f'RSI ({rsi_period})',
                                                f'Williams %R ({will_period})', '거래량'),
                                row_width=[0.2, 0.15, 0.15, 0.4])

            # 캔들차트
            fig.add_trace(go.Candlestick(x=df_ohlcv.index,
                                         open=df_ohlcv['Open'], high=df_ohlcv['High'],
                                         low=df_ohlcv['Low'], close=df_ohlcv['Close'], name="일봉"),
                          row=1, col=1)

            fig.add_hline(y=entry_price, line_dash="dash", line_color="green", annotation_text="ENTRY", row=1, col=1)
            fig.add_hline(y=target_price, line_dash="dash", line_color="blue", annotation_text="TARGET", row=1, col=1)
            fig.add_hline(y=stop_price, line_dash="dash", line_color="red", annotation_text="STOP", row=1, col=1)

            if upper_res is not None:
                fig.add_hrect(y0=upper_res['Bin_Bottom'], y1=upper_res['Bin_Top'], line_width=0, fillcolor="red",
                              opacity=0.1, row=1, col=1)
                fig.add_hline(y=upper_res['Bin_Center'], line_dash="dot", line_color="red",
                              annotation_text=f"상방 저항: {int(upper_res['Bin_Center']):,.0f}원 ({upper_res['Vol_Pct']:.1f}%)",
                              annotation_position="top right", row=1, col=1)

            if lower_sup is not None:
                fig.add_hrect(y0=lower_sup['Bin_Bottom'], y1=lower_sup['Bin_Top'], line_width=0, fillcolor="blue",
                              opacity=0.1, row=1, col=1)
                fig.add_hline(y=lower_sup['Bin_Center'], line_dash="dot", line_color="blue",
                              annotation_text=f"하방 지지: {int(lower_sup['Bin_Center']):,.0f}원 ({lower_sup['Vol_Pct']:.1f}%)",
                              annotation_position="bottom right", row=1, col=1)

            # RSI 차트
            fig.add_trace(go.Scatter(x=df_ohlcv.index, y=df_ohlcv['RSI'], line=dict(color='orange'), name="RSI"), row=2,
                          col=1)
            fig.add_hline(y=70, line_dash="dash", line_color="red", row=2, col=1)
            fig.add_hline(y=30, line_dash="dash", line_color="green", row=2, col=1)

            # Williams %R 차트
            fig.add_trace(
                go.Scatter(x=df_ohlcv.index, y=df_ohlcv['Williams_R'], line=dict(color='cyan'), name="Will %R"), row=3,
                col=1)
            fig.add_hline(y=-20, line_dash="dash", line_color="red", row=3, col=1)
            fig.add_hline(y=-80, line_dash="dash", line_color="green", row=3, col=1)

            # 거래량 차트
            colors = ['red' if row['Close'] > row['Open'] else 'blue' for _, row in df_ohlcv.iterrows()]
            fig.add_trace(go.Bar(x=df_ohlcv.index, y=df_ohlcv['Volume'], name="거래량", marker_color=colors), row=4, col=1)

            fig.update_layout(height=800, template="plotly_dark", xaxis_rangeslider_visible=False)
            st.plotly_chart(fig, use_container_width=True)

            # --- 4. 수급 및 종합 분석 영역 ---
            st.markdown("---")
            st.markdown("### 4. 수급 및 종합 분석 영역")
            col_inv, col_vol = st.columns([2, 1])

            with col_inv:
                # 🌟 [수정됨]: 데이터 수집 출처(KRX vs 네이버 증권)에 따라 표 제목의 단위를 '억원' 또는 '주 수량'으로 동적 스위칭
                if is_naver_fallback:
                    st.markdown("#### 🏢 메이저 수급 및 거래량 동향 (최근 10일, 단위: 주 [네이버 우회 수집])")
                else:
                    st.markdown("#### 🏢 메이저 수급 및 거래량 동향 (최근 10일, 단위: 억원)")

                # 🌟 [수정됨]: 네이버 증권 데이터(주 수량)일 경우 소수점 포맷 대신 가독성 좋은 정수형 컴마(,0f) 포맷으로 변환하도록 딕셔너리 정밀 수정
                format_dict = {}
                if '외국인' in df_display.columns:
                    format_dict['외국인'] = '{:,.0f}' if is_naver_fallback else '{:.1f}'
                if '기관합계' in df_display.columns:
                    format_dict['기관합계'] = '{:,.0f}' if is_naver_fallback else '{:.1f}'
                if '개인' in df_display.columns:
                    format_dict['개인'] = '{:.1f}'

                st.dataframe(df_display.sort_index(ascending=False).style.format(format_dict), use_container_width=True)

            with col_vol:
                st.markdown("#### 💡 종합 분석 요약 (Summary)")

                total_days = len(df_merged)
                last_day_data = df_merged.iloc[-1]
                last_date_str = df_merged.index[-1]
                last_vol_change = last_day_data.get('Vol_Change_Pct', 0)

                # 수급 데이터가 완전히 비어있지 않고 컬럼들이 정상 존재할 때 요약문 생성
                if '외국인' in df_merged.columns and '기관합계' in df_merged.columns:
                    foreign_buy_days = (df_merged['외국인'] > 0).sum()
                    is_yangmaesu = (last_day_data['외국인'] > 0) and (last_day_data['기관합계'] > 0)

                    # 🌟 [수정됨]: 데이터 출처(수량 vs 금액)에 구애받지 않도록 '순매수 성향(>0)' 기준으로 공통 분석을 안전하게 수행
                    dynamic_summary = f"**{stock_name}**는 최근 {total_days}거래일 중 {foreign_buy_days}일 동안 외국인 주도의 매집이 확인됩니다. "

                    if pd.notnull(last_vol_change) and last_vol_change >= 50 and is_yangmaesu:
                        dynamic_summary += f"특히 {last_date_str}에는 거래량이 전일 대비 {last_vol_change:.0f}% 급증하며 외인/기관 양매수 패턴이 발생했습니다. "
                    elif is_yangmaesu:
                        dynamic_summary += f"{last_date_str} 기준 외인/기관 양매수 패턴이 발생하여 긍정적인 수급이 확인됩니다. "
                    elif pd.notnull(last_vol_change) and last_vol_change >= 50:
                        dynamic_summary += f"특히 {last_date_str}에는 거래량이 전일 대비 {last_vol_change:.0f}% 급증하며 의미 있는 변동성이 나타났습니다. "
                    else:
                        dynamic_summary += "최근 거래량의 급격한 폭발이나 뚜렷한 양매수 패턴은 관찰되지 않고 있습니다. "
                else:
                    # 데이터 전면 차단 시 거래량 기반으로 요약
                    dynamic_summary = f"**{stock_name}**의 최근 거래량 동향입니다. "
                    if pd.notnull(last_vol_change) and last_vol_change >= 50:
                        dynamic_summary += f"특히 {last_date_str}에는 거래량이 전일 대비 {last_vol_change:.0f}% 급증하며 의미 있는 변동성이 나타났습니다. "
                    else:
                        dynamic_summary += "최근 거래량의 급격한 폭발은 관찰되지 않고 있습니다. "

                dynamic_summary += f"이는 {entry_price:,.0f}원의 지지 매물대를 지켜내려는 수급 신호로 해석될 수 있으며, 목표가 {target_price:,.0f}원 달성 가능성을 높입니다."

                # 🌟 [수정됨]: 네이버 증권 우회 엔진 작동 시 사용자에게 현 상황을 투명하게 안내하는 커스텀 UI 경고 보완 (개인 데이터 누락 안내 포함)
                if is_naver_fallback:
                    st.warning(
                        "⚠️ 현재 클라우드 배포 서버가 KRX 데이터 공급처로부터 제한되어 **[네이버 증권]** 시스템으로 실시간 우회 연동되었습니다. 이에 따라 수급 단위가 '수량(주)'으로 표기되며 개인 데이터는 제외됩니다. (거래량 추이는 100% 정상 제공)")
                elif df_investor.empty:
                    st.error("⚠️ 모든 수급 채널의 접속이 일시 차단되었습니다. 거래량 중심의 분석 데이터만 제공됩니다.")

                st.info(dynamic_summary)

        except Exception as e:
            st.error(f"데이터를 불러오는 중 내부 오류가 발생했습니다: {e}")
            st.warning("일시적인 서버 문제이거나 상장 폐지된 종목일 수 있습니다.")
    else:
        st.error("⚠️ 잘못된 종목명이나 코드를 입력하셨습니다. 다시 확인해 주세요.")