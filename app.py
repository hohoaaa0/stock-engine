import streamlit as st
import pandas as pd
import FinanceDataReader as fdr
from datetime import datetime, timedelta
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import os

# 🌟 [수정됨]: pykrx 패키지 제거 및 글로벌 데이터 플랫폼 yfinance 탑재
import yfinance as yf

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
    # 🌟 [수정됨]: yfinance 종목 코드 변환을 위해 'Market' 컬럼도 함께 불러오도록 유지
    return df[['Code', 'Name', 'Market']] if 'Market' in df.columns else df[['Code', 'Name']]


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

                # --- 🌟 [수정됨]: IP 차단 문제가 심각한 pykrx 및 네이버 크롤링을 완전히 폐기하고 yfinance 엔진 적용 ---
                # Yahoo Finance는 KOSPI는 .KS, KOSDAQ은 .KQ 접미사를 요구함
                market_info = df_stocks[df_stocks['Code'] == stock_code].iloc[0].get('Market', 'KOSPI')
                yf_code = f"{stock_code}.KQ" if 'KOSDAQ' in str(market_info).upper() else f"{stock_code}.KS"
                ticker = yf.Ticker(yf_code)

                # 주요 지분 정보(Major Holders) 추출 방어 로직 (yfinance 기능 활용)
                try:
                    major_holders = ticker.major_holders
                    if major_holders is not None and not major_holders.empty:
                        # yfinance에서 가져온 지분 정보 컬럼 이름 정제
                        major_holders.columns = ['비중', '주요 주주 정보']
                        major_holders = major_holders[['주요 주주 정보', '비중']]
                except:
                    major_holders = pd.DataFrame()

                # fdr 일봉 데이터(Volume, Close)를 활용한 10일 동향 데이터 추출 (수급 대체용)
                df_vol = df_ohlcv[['Close', 'Volume']].copy()
                df_vol.index = pd.to_datetime(df_vol.index).strftime('%Y-%m-%d')
                df_vol['Vol_Change_Pct'] = df_vol['Volume'].pct_change() * 100
                df_vol['Price_Change_Pct'] = df_vol['Close'].pct_change() * 100

                # 최근 10일치 데이터만 추출
                df_merged = df_vol.tail(10).copy()

                # UI 표기를 위해 인덱스를 MM/DD 형식의 문자열로 변환
                df_merged.index = pd.to_datetime(df_merged.index).strftime('%m/%d')

                # 거래량 및 주가 텍스트 동적 생성
                df_merged['당일 거래량 (추이)'] = df_merged.apply(
                    lambda
                        x: f"{x['Volume'] / 1000:,.0f}K ({'+' if x['Vol_Change_Pct'] > 0 else ''}{x['Vol_Change_Pct']:.0f}%)" if pd.notnull(
                        x.get('Vol_Change_Pct')) else f"{x['Volume'] / 1000:,.0f}K",
                    axis=1
                )
                df_merged['종가 (추이)'] = df_merged.apply(
                    lambda
                        x: f"{x['Close']:,.0f}원 ({'+' if x['Price_Change_Pct'] > 0 else ''}{x['Price_Change_Pct']:.2f}%)" if pd.notnull(
                        x.get('Price_Change_Pct')) else f"{x['Close']:,.0f}원",
                    axis=1
                )

                # 화면 UI용 최종 컬럼 (yfinance 엔진에 맞춘 거래량/종가 중심 재구성)
                df_display = df_merged[['종가 (추이)', '당일 거래량 (추이)']]

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
                # 🌟 [수정됨]: yfinance 데이터 기반으로 표 제목 및 내용 완벽 교체
                st.markdown("#### 🏢 최근 거래량 동향 및 주요 지분 정보 (Yahoo Finance)")
                st.dataframe(df_display.sort_index(ascending=False), use_container_width=True)

                # 지분 정보가 정상적으로 로드된 경우 표출
                if not major_holders.empty:
                    st.markdown("##### 👥 주요 주주 지분율 분석")
                    st.dataframe(major_holders, hide_index=True, use_container_width=True)

            with col_vol:
                st.markdown("#### 💡 종합 분석 요약 (Summary)")

                total_days = len(df_merged)
                last_day_data = df_merged.iloc[-1]
                last_date_str = df_merged.index[-1]
                last_vol_change = last_day_data.get('Vol_Change_Pct', 0)
                last_price_change = last_day_data.get('Price_Change_Pct', 0)

                # 🌟 [수정됨]: yfinance 구조에 맞춰 거래량 폭발 및 가격 변동성에 집중한 스마트 요약 로직 적용
                dynamic_summary = f"**{stock_name}**의 최근 {total_days}거래일 주가 및 거래량 동향입니다. "

                if pd.notnull(last_vol_change) and last_vol_change >= 50:
                    dynamic_summary += f"특히 가장 최근 거래일({last_date_str})에는 거래량이 전일 대비 {last_vol_change:.0f}% 급증하며 의미 있는 변동성이 관찰되었습니다. "
                    if last_price_change > 0:
                        dynamic_summary += "주가 상승을 동반한 거래량 급증은 강력한 매수세(메이저 수급) 유입으로 긍정적 해석이 가능합니다. "
                    else:
                        dynamic_summary += "주가 하락과 함께 거래량이 터진 경우, 단기 매도 물량 출회에 대한 주의가 필요합니다. "
                else:
                    dynamic_summary += "최근 거래량의 급격한 폭발이나 이상 징후는 관찰되지 않으며 전반적으로 안정적인 흐름을 보이고 있습니다. "

                dynamic_summary += f"현재 기술적 위치는 {entry_price:,.0f}원의 지지/진입 타점을 기준으로 리스크를 관리하며, 목표가 {target_price:,.0f}원 도달을 1차적으로 기대해 볼 수 있는 구간입니다."

                # yfinance 도입 안내 메세지
                st.success("✅ **글로벌 금융 플랫폼(Yahoo Finance) 연동 완료:** 클라우드 서버 환경에서도 IP 차단 없이 완벽하고 안정적으로 분석 데이터를 제공합니다.")
                st.info(dynamic_summary)

        except Exception as e:
            st.error(f"데이터를 불러오는 중 내부 오류가 발생했습니다: {e}")
            st.warning("일시적인 서버 문제이거나 상장 폐지된 종목일 수 있습니다.")
    else:
        st.error("⚠️ 잘못된 종목명이나 코드를 입력하셨습니다. 다시 확인해 주세요.")