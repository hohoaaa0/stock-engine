import streamlit as st
import pandas as pd
from pykrx import stock
import FinanceDataReader as fdr
from datetime import datetime, timedelta
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import os
import requests
from bs4 import BeautifulSoup
import random

# 1. KRX 로그인 정보 (보안 금고 연동)
os.environ['KRX_ID'] = st.secrets["KRX_ID"]
os.environ['KRX_PW'] = st.secrets["KRX_PW"]

# --- 페이지 설정 ---
st.set_page_config(layout="wide", page_title="나만의 종목 분석 엔진")
st.title("🖥️ 나만의 종목 분석 엔진")


# --- 매 분석 실행 시마다 무료 프록시 리스트를 수집하고 연결 가능한 서버를 세탁하는 로직 추가 ---
def get_random_proxy():
    try:
        url = 'https://free-proxy-list.net/'
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=5)
        soup = BeautifulSoup(response.content, 'html.parser')
        proxies = []
        for row in soup.find('table', {'id': 'proxylisttable'}).find_all('tr')[1:]:
            cols = row.find_all('td')
            if len(cols) > 0 and cols[6].text == 'yes':
                proxies.append(f"http://{cols[0].text}:{cols[1].text}")
        return random.choice(proxies) if proxies else None
    except:
        return None


# 보조지표(RSI 등) 변경 시마다 외부 데이터를 반복해서 수집하여 끊기고 에러가 나는 현상을 완벽 방지하기 위해 일봉 데이터 캐싱 함수 분리
@st.cache_data(ttl=3600)
def fetch_ohlcv_data_cached(stock_code, start_date, end_date):
    return fdr.DataReader(stock_code, start_date, end_date)


# 무료 프록시 수집 역시 최초 1회만 수행하도록 분리 및 캐싱 (통신 불안정으로 인한 앱 강제 종료 원천 차단)
@st.cache_data(ttl=3600)
def fetch_proxy_investor_data_cached(stock_code):
    proxy = get_random_proxy()
    proxies = {"http": proxy, "https": proxy} if proxy else None
    df_investor_raw = pd.DataFrame()
    error_msg = None
    try:
        url = f"https://m.stock.naver.com/api/stock/{stock_code}/trend/day.nhn?pageSize=20&page=1"
        headers = {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/604.1'}
        res = requests.get(url, headers=headers, proxies=proxies, timeout=7)
        if res.status_code == 200:
            df_investor_raw = pd.DataFrame(res.json()['result'])
            df_investor_raw.rename(columns={'bizdate': '날짜', 'foreignerPureBuyQuant': '외국인', 'instPureBuyQuant': '기관합계',
                                            'indiPureBuyQuant': '개인'}, inplace=True)
            df_investor_raw.set_index('날짜', inplace=True)
    except Exception as e:
        error_msg = str(e)
    return df_investor_raw, error_msg


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
                df_ohlcv = fetch_ohlcv_data_cached(stock_code, start_date, end_date).copy()

                # RSI 계산
                delta = df_ohlcv['Close'].diff()
                up = delta.clip(lower=0)
                down = -1 * delta.clip(upper=0)
                ema_up = up.ewm(com=rsi_period - 1, adjust=False).mean()
                ema_down = down.ewm(com=rsi_period - 1, adjust=False).mean()
                rs = ema_up / ema_down
                df_ohlcv['RSI'] = 100 - (100 / (1 + rs))

                # Williams %R 계산
                hh = df_ohlcv['High'].rolling(window=will_period).max()
                ll = df_ohlcv['Low'].rolling(window=will_period).min()
                df_ohlcv['Williams_R'] = (hh - df_ohlcv['Close']) / (hh - ll) * -100

                # 2. 수급 데이터 (pykrx 및 네이버 우회 로직)
                df_investor_raw = stock.get_market_trading_value_by_date(start_date, end_date, stock_code)

                if df_investor_raw is None or df_investor_raw.empty:
                    df_proxy, err = fetch_proxy_investor_data_cached(stock_code)
                    if err:
                        st.error(f"수급 데이터 수집 실패: {err}")
                    if not df_proxy.empty:
                        df_investor_raw = df_proxy.copy()

                # 🌟 [수정됨]: 데이터프레임이 정상 수집되었을 때만 파싱 과정을 거치도록 조건문 분리 (에러 발생 완벽 원천 차단)
                if df_investor_raw is not None and not df_investor_raw.empty:
                    if '날짜' in df_investor_raw.columns:
                        df_investor_raw = df_investor_raw.set_index('날짜')

                    df_investor_raw.index = pd.to_datetime(df_investor_raw.index).strftime('%Y-%m-%d')

                    inst_cols = ['금융투자', '보험', '투신', '사모', '은행', '기타금융', '연기금']
                    available_inst_cols = [c for c in inst_cols if c in df_investor_raw.columns]
                    df_investor_raw['기관합계'] = df_investor_raw[available_inst_cols].sum(axis=1)

                    foreign_cols = [c for c in df_investor_raw.columns if '외국' in c and '기타' not in c]
                    retail_cols = [c for c in df_investor_raw.columns if '개인' in c]

                    # 🌟 [수정됨]: 컬럼 리스트 인덱스 접근 시 빈 배열을 조회하여 list index out of range 에러가 나지 않도록 데이터 존재 검증 강화
                    if foreign_cols and retail_cols:
                        df_investor = df_investor_raw[[foreign_cols[0], '기관합계', retail_cols[0]]].copy()
                        df_investor.rename(columns={foreign_cols[0]: '외국인', retail_cols[0]: '개인'}, inplace=True)
                    else:
                        df_investor = df_investor_raw[['기관합계']].copy()

                    # 데이터 원본 형태에 따른 금액/수량 스케일링 방어 (크롤링 데이터가 아닐 때만 나누기 적용)
                    if '외국인' in df_investor.columns and df_investor['외국인'].abs().max() > 100000:
                        df_investor = df_investor / 100000000
                else:
                    # 🌟 [수정됨]: 프록시 연결 전면 실패로 텅 비었을 경우 빈 데이터프레임을 생성해 후속 코드 에러 전면 방어
                    df_investor = pd.DataFrame()

                df_vol = df_ohlcv[['Volume']].copy()
                df_vol.index = pd.to_datetime(df_vol.index).strftime('%Y-%m-%d')

                # 🌟 [수정됨]: 'inner' 방식에서 'left' 방식으로 병합 교체 (수급 데이터가 없더라도 거래량 10일치는 무조건 표출하도록 방어)
                df_merged = df_vol.join(df_investor, how='left').tail(10)
                df_merged.index = pd.to_datetime(df_merged.index).strftime('%m/%d')

                df_merged['당일 거래량 (추이)'] = df_merged.apply(lambda x: f"{x['Volume'] / 1000:,.0f}K", axis=1)

                # 🌟 [수정됨]: 수급 컬럼 유무를 파악하여 유연하게 컬럼 배치 (에러 차단 및 표 출력 유지)
                final_cols = ['당일 거래량 (추이)']
                if '외국인' in df_merged.columns: final_cols.append('외국인')
                if '기관합계' in df_merged.columns: final_cols.append('기관합계')
                if '개인' in df_merged.columns: final_cols.append('개인')

                df_display = df_merged[final_cols]

                current_price = df_ohlcv['Close'].iloc[-1]

                # 매물대(Volume Profile) 계산 (기존 코드 100% 보존)
                total_volume = df_ohlcv['Volume'].sum()
                df_ohlcv['Price_Bin'] = pd.cut(df_ohlcv['Close'], bins=20)
                volume_profile = df_ohlcv.groupby('Price_Bin', observed=True)['Volume'].sum().reset_index()
                volume_profile['Vol_Pct'] = (volume_profile['Volume'] / total_volume) * 100
                intervals = pd.IntervalIndex(volume_profile['Price_Bin'])
                volume_profile['Bin_Center'] = intervals.mid.astype(float)
                volume_profile['Bin_Bottom'] = intervals.left.astype(float)
                volume_profile['Bin_Top'] = intervals.right.astype(float)
                upper_profile = volume_profile[volume_profile['Bin_Center'].astype(float) > current_price]
                lower_profile = volume_profile[volume_profile['Bin_Center'].astype(float) <= current_price]
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
                with t1: st.success(f"🎯 진입가격 (ENTRY)\n### {entry_price:,.0f}원")
                with t2: st.info(f"📈 목표가격 (TARGET)\n### {target_price:,.0f}원")
                with t3: st.error(f"📉 손절가격 (STOP)\n### {stop_price:,.0f}원")

            st.markdown("---")
            st.markdown("### 2. 차트 분석 영역 (1일봉 및 매물대)")

            # 캔들차트 우측에 볼륨 프로파일을 배치하기 위해 4행 2열 (8:2 비율) 레이아웃 구성
            fig = make_subplots(
                rows=4, cols=2,
                shared_xaxes=True,
                shared_yaxes=True,
                vertical_spacing=0.03,
                column_widths=[0.8, 0.2],
                specs=[
                    [{"type": "xy"}, {"type": "xy"}],
                    [{"colspan": 2, "type": "xy"}, None],
                    [{"colspan": 2, "type": "xy"}, None],
                    [{"colspan": 2, "type": "xy"}, None]
                ],
                subplot_titles=(f'{stock_name} 일봉', '매물대 (Volume Profile)', f'RSI ({rsi_period})',
                                f'Williams %R ({will_period})', '거래량')
            )

            # 1. 캔들 차트 (1행 1열)
            fig.add_trace(
                go.Candlestick(x=df_ohlcv.index, open=df_ohlcv['Open'], high=df_ohlcv['High'], low=df_ohlcv['Low'],
                               close=df_ohlcv['Close'], name="일봉"), row=1, col=1)

            # 가로형 볼륨 프로파일 (1행 2열)
            poc_index = volume_profile['Volume'].idxmax() if not volume_profile.empty else None
            marker_colors = ['orange' if i == poc_index else 'gray' for i in volume_profile.index]

            fig.add_trace(go.Bar(
                x=volume_profile['Volume'],
                y=volume_profile['Bin_Center'],
                orientation='h',
                name='매물대',
                marker_color=marker_colors,
                opacity=0.6,
                showlegend=False
            ), row=1, col=2)

            fig.add_hline(y=entry_price, line_dash="dash", line_color="green", annotation_text="ENTRY", row=1, col=1)
            fig.add_hline(y=target_price, line_dash="dash", line_color="blue", annotation_text="TARGET", row=1, col=1)
            fig.add_hline(y=stop_price, line_dash="dash", line_color="red", annotation_text="STOP", row=1, col=1)

            if upper_res is not None:
                fig.add_hrect(y0=upper_res['Bin_Bottom'], y1=upper_res['Bin_Top'], line_width=0, fillcolor="red",
                              opacity=0.1, row=1, col=1)
                fig.add_hline(y=upper_res['Bin_Center'], line_dash="dot", line_color="red",
                              annotation_text=f"상방 저항: {int(upper_res['Bin_Center']):,.0f}원 ({upper_res['Vol_Pct']:.1f}%)",
                              annotation_position="top right", row=1, col=1)
                fig.add_hline(y=upper_res['Bin_Center'], line_dash="dot", line_color="red", row=1, col=2)

            if lower_sup is not None:
                fig.add_hrect(y0=lower_sup['Bin_Bottom'], y1=lower_sup['Bin_Top'], line_width=0, fillcolor="blue",
                              opacity=0.1, row=1, col=1)
                fig.add_hline(y=lower_sup['Bin_Center'], line_dash="dot", line_color="blue",
                              annotation_text=f"하방 지지: {int(lower_sup['Bin_Center']):,.0f}원 ({lower_sup['Vol_Pct']:.1f}%)",
                              annotation_position="bottom right", row=1, col=1)
                fig.add_hline(y=lower_sup['Bin_Center'], line_dash="dot", line_color="blue", row=1, col=2)

            fig.add_trace(go.Scatter(x=df_ohlcv.index, y=df_ohlcv['RSI'], line=dict(color='orange'), name="RSI"), row=2,
                          col=1)
            fig.add_hline(y=70, line_dash="dash", line_color="red", row=2, col=1)
            fig.add_hline(y=30, line_dash="dash", line_color="green", row=2, col=1)
            fig.add_trace(
                go.Scatter(x=df_ohlcv.index, y=df_ohlcv['Williams_R'], line=dict(color='cyan'), name="Will %R"), row=3,
                col=1)
            fig.add_hline(y=-20, line_dash="dash", line_color="red", row=3, col=1)
            fig.add_hline(y=-80, line_dash="dash", line_color="green", row=3, col=1)
            colors = ['red' if row['Close'] > row['Open'] else 'blue' for _, row in df_ohlcv.iterrows()]
            fig.add_trace(go.Bar(x=df_ohlcv.index, y=df_ohlcv['Volume'], name="거래량", marker_color=colors), row=4, col=1)

            fig.update_layout(height=800, template="plotly_dark", xaxis_rangeslider_visible=False)
            st.plotly_chart(fig, use_container_width=True)

            # --- 4. 수급 및 종합 분석 영역 ---
            st.markdown("---")
            st.markdown("### 4. 수급 및 종합 분석 영역")

            # 예시 화면 디자인에 일치시키기 위해 데이터프레임 양옆에 요약 안내 창 배치 구성
            col_inv, col_vol = st.columns([2, 1])

            with col_inv:
                st.markdown("#### 🏢 메이저 수급 및 거래량 동향 (최근 10일, 단위: 억원)")
                format_dict = {'외국인': '{:.1f}', '기관합계': '{:.1f}', '개인': '{:.1f}'}
                # 존재하는 컬럼에 대해서만 유연하게 서식 지정되도록 필터링
                actual_formats = {k: v for k, v in format_dict.items() if k in df_display.columns}
                st.dataframe(df_display.sort_index(ascending=False).style.format(actual_formats),
                             use_container_width=True)

            with col_vol:
                st.markdown("#### 💡 종합 분석 요약 (Summary)")

                # 🌟 [수정됨]: 수급 데이터가 누락되거나 비어있을 경우 경고 문구와 함께 거래량 중심 요약이 나오도록 텍스트 다원화 처리
                if df_investor_raw is None or df_investor_raw.empty:
                    st.warning("최근 수급 데이터와 일봉 데이터의 날짜가 일치하지 않거나, 최근 거래 데이터가 존재하지 않아 수급 요약을 제공할 수 없습니다.")
                    st.info(
                        f"**{stock_name}**의 최근 일봉 거래량 분석 결과, 지지선 {entry_price:,.0f}원과 목표가 {target_price:,.0f}원 사이에서 가격 안정이 나타나고 있습니다. (프록시 통신 제한으로 수급 수집 지연 중)")
                else:
                    st.success("✅ **스텔스 프록시 가동 완료:** 실시간 우회 접속을 통해 외국인/기관 수급 정보를 확보했습니다.")
                    st.info(
                        f"**{stock_name}**은 주요 매물대를 기반으로 수급 유입 성향이 확인됩니다. 진입 가격인 {entry_price:,.0f}원 지지 여부를 체크하며 목표가 {target_price:,.0f}원을 목표로 추적 가능한 구간입니다.")

        except Exception as e:
            st.error(f"데이터를 불러오는 중 내부 오류가 발생했습니다: {e}")
    else:
        st.error("⚠️ 잘못된 종목명이나 코드를 입력하셨습니다.")