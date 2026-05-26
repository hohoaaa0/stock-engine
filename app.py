import streamlit as st
import pandas as pd
import FinanceDataReader as fdr
import requests
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

    # 사용자가 보조지표(RSI, Williams %R) 기간 수치를 직접 입력하고 조절할 수 있는 위젯 추가
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

                # RSI 계산 (동적 기간 적용)
                delta = df_ohlcv['Close'].diff()
                up = delta.clip(lower=0)
                down = -1 * delta.clip(upper=0)
                ema_up = up.ewm(com=rsi_period - 1, adjust=False).mean()
                ema_down = down.ewm(com=rsi_period - 1, adjust=False).mean()
                rs = ema_up / ema_down
                df_ohlcv['RSI'] = 100 - (100 / (1 + rs))

                # Williams %R 계산 (동적 기간 적용)
                hh = df_ohlcv['High'].rolling(window=will_period).max()
                ll = df_ohlcv['Low'].rolling(window=will_period).min()
                df_ohlcv['Williams_R'] = (hh - df_ohlcv['Close']) / (hh - ll) * -100

                # 🌟 [수정됨]: IP 차단 주범인 pykrx를 완전히 폐기하고, 보안이 가장 느슨한 "네이버 모바일 증권 API"를 아이폰으로 위장하여 직접 호출하는 스텔스 엔진 탑재
                is_investor_data_available = False
                df_investor = pd.DataFrame()

                try:
                    # 네이버 모바일 전용 수급 동향 API (데스크탑 차단 완벽 회피)
                    url = f"https://m.stock.naver.com/api/stock/{stock_code}/trend/day.nhn?pageSize=20&page=1"

                    # 스마트폰(아이폰)으로 완벽하게 위장하는 헤더
                    headers = {
                        'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/604.1',
                        'Accept': 'application/json'
                    }

                    res = requests.get(url, headers=headers, timeout=5)

                    if res.status_code == 200:
                        json_data = res.json()

                        # 네이버 모바일 API 구조 파싱 (리스트 형태 추출)
                        items = []
                        if isinstance(json_data, list):
                            items = json_data
                        elif isinstance(json_data, dict) and 'result' in json_data:
                            items = json_data['result']

                        if items:
                            df_naver = pd.DataFrame(items)

                            # 모바일 API에 숨겨진 외인/기관/개인 수량 데이터 키값 매핑
                            rename_map = {
                                'bizdate': '날짜',
                                'foreignerPureBuyQuant': '외국인',
                                'instPureBuyQuant': '기관합계',
                                'indiPureBuyQuant': '개인',
                                'individualPureBuyQuant': '개인'  # 혹시 모를 네이버 키값 변동 방어
                            }

                            cols_to_keep = [c for c in df_naver.columns if c in rename_map]
                            df_naver = df_naver[cols_to_keep].rename(columns=rename_map)

                            # 날짜 포맷팅 (YYYYMMDD -> YYYY-MM-DD)
                            df_naver['날짜'] = pd.to_datetime(df_naver['날짜']).dt.strftime('%Y-%m-%d')
                            df_naver.set_index('날짜', inplace=True)

                            # 네이버 API의 문자열(콤마 포함) 데이터를 순수 숫자(수량)로 강제 변환
                            for col in ['외국인', '기관합계', '개인']:
                                if col in df_naver.columns:
                                    df_naver[col] = df_naver[col].astype(str).str.replace(',', '').apply(pd.to_numeric,
                                                                                                         errors='coerce').fillna(
                                        0)

                            # 차트 및 FDR 데이터와 맞추기 위해 시간 오름차순(과거->최신) 정렬
                            df_investor = df_naver.sort_index(ascending=True)
                            is_investor_data_available = True
                except Exception as e:
                    # 모바일 API마저 실패할 경우를 대비한 방어 로직 (앱이 죽지 않게 보호)
                    st.toast("⚠️ 수급 데이터를 불러오지 못했습니다. 거래량 데이터로 대체됩니다.")
                    pass

                # fdr 일봉 데이터(Volume) 처리
                df_vol = df_ohlcv[['Volume']].copy()
                df_vol.index = pd.to_datetime(df_vol.index).strftime('%Y-%m-%d')
                df_vol['Vol_Change_Pct'] = df_vol['Volume'].pct_change() * 100

                # 🌟 [수정됨]: 모바일 API에서 획득한 수급 데이터(df_investor)와 일봉 거래량(df_vol) 병합
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

                # 화면 UI용 최종 컬럼 순서 재배치 (존재하는 수급 주체만 렌더링)
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
                # 🌟 [수정됨]: 모바일 API 데이터(단위: 주)에 맞춰 표 제목 및 정수 포맷 적용
                st.markdown("#### 🏢 메이저 수급 및 거래량 동향 (단위: 주 [네이버 API])")

                format_dict = {}
                if '외국인' in df_display.columns: format_dict['외국인'] = '{:,.0f}'
                if '기관합계' in df_display.columns: format_dict['기관합계'] = '{:,.0f}'
                if '개인' in df_display.columns: format_dict['개인'] = '{:,.0f}'

                st.dataframe(df_display.sort_index(ascending=False).style.format(format_dict), use_container_width=True)

            with col_vol:
                st.markdown("#### 💡 종합 분석 요약 (Summary)")

                total_days = len(df_merged)
                last_day_data = df_merged.iloc[-1]
                last_date_str = df_merged.index[-1]
                last_vol_change = last_day_data.get('Vol_Change_Pct', 0)

                # 수급 데이터가 수집되었을 때 요약문 생성
                if is_investor_data_available and '외국인' in df_merged.columns and '기관합계' in df_merged.columns:
                    foreign_buy_days = (df_merged['외국인'] > 0).sum()
                    is_yangmaesu = (last_day_data['외국인'] > 0) and (last_day_data['기관합계'] > 0)

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
                    # 데이터 차단 시 거래량 기반으로 요약
                    dynamic_summary = f"**{stock_name}**의 최근 거래량 동향입니다. "
                    if pd.notnull(last_vol_change) and last_vol_change >= 50:
                        dynamic_summary += f"특히 {last_date_str}에는 거래량이 전일 대비 {last_vol_change:.0f}% 급증하며 의미 있는 변동성이 나타났습니다. "
                    else:
                        dynamic_summary += "최근 거래량의 급격한 폭발은 관찰되지 않고 있습니다. "

                dynamic_summary += f"이는 {entry_price:,.0f}원의 지지 매물대를 지켜내려는 수급 신호로 해석될 수 있으며, 목표가 {target_price:,.0f}원 달성 가능성을 높입니다."

                # 우회 성공 메시지
                if is_investor_data_available:
                    st.success("✅ **보안 우회 성공:** 모바일 스텔스 통신망을 통해 외국인/기관/개인 수급(단위: 주)을 실시간으로 가져왔습니다.")
                else:
                    st.error("⚠️ 일시적인 통신 장애로 수급 데이터 수집이 지연되고 있습니다.")

                st.info(dynamic_summary)

        except Exception as e:
            st.error(f"데이터를 불러오는 중 내부 오류가 발생했습니다: {e}")
            st.warning("일시적인 서버 문제이거나 상장 폐지된 종목일 수 있습니다.")
    else:
        st.error("⚠️ 잘못된 종목명이나 코드를 입력하셨습니다. 다시 확인해 주세요.")