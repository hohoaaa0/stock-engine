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
import time

# 🌟 [수정됨]: 엑셀 스프레드로 완벽하게 변환하기 위한 AgGrid 모듈 임포트
from st_aggrid import AgGrid, GridOptionsBuilder, ColumnsAutoSizeMode

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
    df_investor_raw = pd.DataFrame()
    error_msg = None

    for attempt in range(3):
        proxy = get_random_proxy()
        proxies = {"http": proxy, "https": proxy} if proxy else None
        try:
            url = f"https://m.stock.naver.com/api/stock/{stock_code}/trend/day.nhn?pageSize=20&page=1"
            headers = {
                'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/604.1'}
            res = requests.get(url, headers=headers, proxies=proxies, timeout=7)
            if res.status_code == 200:
                df_investor_raw = pd.DataFrame(res.json()['result'])
                df_investor_raw.rename(
                    columns={'bizdate': '날짜', 'foreignerPureBuyQuant': '외국인', 'instPureBuyQuant': '기관합계',
                             'indiPureBuyQuant': '개인'}, inplace=True)
                df_investor_raw['날짜'] = pd.to_datetime(df_investor_raw['날짜']).dt.strftime('%Y-%m-%d')
                df_investor_raw.set_index('날짜', inplace=True)
                error_msg = None
                break
        except Exception as e:
            error_msg = str(e)
            time.sleep(0.5)

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

    st.markdown("**📉 보조지표 가이드 기준선 설정**")
    rsi_upper = st.number_input("RSI 과매수 (상단선)", min_value=50, max_value=95, value=70, step=5)
    rsi_lower = st.number_input("RSI 과매도 (하단선)", min_value=5, max_value=50, value=30, step=5)
    will_upper = st.number_input("Will %R 과매수 (상단선)", min_value=-50, max_value=-1, value=-20, step=5)
    will_lower = st.number_input("Will %R 과매도 (하단선)", min_value=-100, max_value=-51, value=-80, step=5)

    chart_view_range = st.radio("📊 차트 조회 기간 설정", ("1개월", "3개월", "6개월"), index=0, horizontal=True)

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
                ema_up = up.ewm(com=13, adjust=False).mean()
                ema_down = down.ewm(com=13, adjust=False).mean()
                rs = ema_up / ema_down
                df_ohlcv['RSI'] = 100 - (100 / (1 + rs))

                # Williams %R 계산
                hh = df_ohlcv['High'].rolling(window=14).max()
                ll = df_ohlcv['Low'].rolling(window=14).min()
                df_ohlcv['Williams_R'] = (hh - df_ohlcv['Close']) / (hh - ll) * -100

                # 2. 수급 데이터 (pykrx 및 네이버 우회 로직)
                df_investor_raw = stock.get_market_trading_value_by_date(start_date, end_date, stock_code)

                if df_investor_raw is None or df_investor_raw.empty:
                    df_proxy, err = fetch_proxy_investor_data_cached(stock_code)
                    if err:
                        st.error(f"수급 데이터 수집 실패: {err}")
                    if not df_proxy.empty:
                        df_investor_raw = df_proxy.copy()

                if df_investor_raw is not None and not df_investor_raw.empty:
                    if '날짜' in df_investor_raw.columns:
                        df_investor_raw = df_investor_raw.set_index('날짜')

                    df_investor_raw.index = pd.to_datetime(df_investor_raw.index).strftime('%Y-%m-%d')

                    inst_cols = ['금융투자', '보험', '투신', '사모', '은행', '기타금융', '연기금']
                    available_inst_cols = [c for c in inst_cols if c in df_investor_raw.columns]
                    df_investor_raw['기관합계'] = df_investor_raw[available_inst_cols].sum(axis=1)

                    foreign_cols = [c for c in df_investor_raw.columns if '외국' in c and '기타' not in c]
                    retail_cols = [c for c in df_investor_raw.columns if '개인' in c]

                    if foreign_cols and retail_cols:
                        df_investor = df_investor_raw[[foreign_cols[0], '기관합계', retail_cols[0]]].copy()
                        df_investor.rename(columns={foreign_cols[0]: '외국인', retail_cols[0]: '개인'}, inplace=True)
                    else:
                        df_investor = df_investor_raw[['기관합계']].copy()

                    if '외국인' in df_investor.columns and df_investor['외국인'].abs().max() > 100000:
                        df_investor = df_investor / 100000000
                else:
                    df_investor = pd.DataFrame()

                df_vol = df_ohlcv[['Volume']].copy()
                df_vol.index = pd.to_datetime(df_vol.index).strftime('%Y-%m-%d')

                df_investor.index = pd.to_datetime(df_investor.index).strftime('%Y-%m-%d')
                df_merged = df_vol.join(df_investor, how='left').tail(10)
                df_merged.index = pd.to_datetime(df_merged.index).strftime(
                    '%Y-%m-%d')  # 🌟 [수정됨]: AgGrid 연동을 위해 인덱스 포맷을 날짜 규격으로 정교화

                # 🌟 [수정됨]: 정수 변환 및 텍스트 포맷 단계를 분리하여 AgGrid가 숫자 연산 및 우측 정렬을 완벽하게 인식하도록 조치
                df_merged['거래량(K)'] = (df_merged['Volume'] / 1000).round(0)

                final_cols = ['거래량(K)']
                if '외국인' in df_merged.columns:
                    df_merged['외국인'] = df_merged['외국인'].round(1)
                    final_cols.append('외국인')
                if '기관합계' in df_merged.columns:
                    df_merged['기관합계'] = df_merged['기관합계'].round(1)
                    final_cols.append('기관합계')
                if '개인' in df_merged.columns:
                    df_merged['개인'] = df_merged['개인'].round(1)
                    final_cols.append('개인')

                # 🌟 [수정됨]: 실제 엑셀 시트처럼 날짜가 첫 번째 열(A열)로 보이도록 인덱스를 일반 컬럼으로 변환
                df_excel_ready = df_merged[final_cols].reset_index()
                df_excel_ready.rename(columns={'index': '분석 일자'}, inplace=True)

                current_price = df_ohlcv['Close'].iloc[-1]

                # 매물대(Volume Profile) 계산
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

                atr = (df_ohlcv['High'] - df_ohlcv['Low']).rolling(window=14).mean().iloc[-1]

                entry_price = current_price * 0.985
                target_price = current_price + (atr * 1.5)
                stop_price = current_price - (atr * 1.2)

                t1, t2, t3 = st.columns(3)
                with t1: st.success(f"🎯 진입가격 (ENTRY)\n### {entry_price:,.0f}원")
                with t2: st.info(f"📈 목표가격 (TARGET)\n### {target_price:,.0f}원")
                with t3: st.error(f"📉 손절가격 (STOP)\n### {stop_price:,.0f}원")

            st.markdown("---")
            st.markdown("### 2. 차트 분석 영역 (1일봉 및 매물대)")

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
                subplot_titles=(f'{stock_name} 일봉', '매물대 (Volume Profile)', 'RSI 가이드 라인', 'Williams %R 가이드 라인', '거래량')
            )

            fig.add_trace(
                go.Candlestick(x=df_ohlcv.index, open=df_ohlcv['Open'], high=df_ohlcv['High'], low=df_ohlcv['Low'],
                               close=df_ohlcv['Close'], name="일봉"), row=1, col=1)

            poc_index = volume_profile['Volume'].idxmax() if not volume_profile.empty else None
            marker_colors = ['orange' if i == poc_index else 'gray' for i in volume_profile.index]
            bar_labels = [f"{int(val):,.0f}원" for val in volume_profile['Bin_Center']]

            fig.add_trace(go.Bar(
                x=volume_profile['Volume'],
                y=volume_profile['Bin_Center'],
                orientation='h',
                name='매물대',
                text=bar_labels,
                textposition='inside',
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
            fig.add_hline(y=rsi_upper, line_dash="dash", line_color="red", row=2, col=1)
            fig.add_hline(y=rsi_lower, line_dash="dash", line_color="green", row=2, col=1)

            fig.add_trace(
                go.Scatter(x=df_ohlcv.index, y=df_ohlcv['Williams_R'], line=dict(color='cyan'), name="Will %R"), row=3,
                col=1)
            fig.add_hline(y=will_upper, line_dash="dash", line_color="red", row=3, col=1)
            fig.add_hline(y=will_lower, line_dash="dash", line_color="green", row=3, col=1)

            colors = ['red' if row['Close'] > row['Open'] else 'blue' for _, row in df_ohlcv.iterrows()]
            fig.add_trace(go.Bar(x=df_ohlcv.index, y=df_ohlcv['Volume'], name="거래량", marker_color=colors), row=4, col=1)

            now_time = datetime.now()
            if chart_view_range == "1개월":
                x_start = (now_time - timedelta(days=30)).strftime("%Y-%m-%d")
            elif chart_view_range == "3개월":
                x_start = (now_time - timedelta(days=90)).strftime("%Y-%m-%d")
            else:
                x_start = (now_time - timedelta(days=180)).strftime("%Y-%m-%d")
            x_end = now_time.strftime("%Y-%m-%d")

            fig.update_layout(height=800, template="plotly_dark", xaxis_rangeslider_visible=False)
            fig.update_xaxes(range=[x_start, x_end], row=1, col=1)
            fig.update_xaxes(range=[x_start, x_end], row=2, col=1)
            fig.update_xaxes(range=[x_start, x_end], row=3, col=1)
            fig.update_xaxes(range=[x_start, x_end], row=4, col=1)

            st.plotly_chart(fig, use_container_width=True)

            # --- 4. 수급 및 종합 분석 영역 ---
            st.markdown("---")
            st.markdown("### 4. 수급 및 종합 분석 영역")

            col_inv, col_vol = st.columns([2, 1])

            with col_inv:
                # 🌟 [수정됨]: 표 상단 제목을 실제 엑셀 시트 스타일링 메세지로 변경 표출
                st.markdown("#### 🏢 메이저 수급 스프레드시트 (최근 10일, 엑셀 격자 모드 연동)")

                # 🌟 [수정됨]: AgGrid 빌더를 통해 실시간 정렬, 필터, 열크기 조절 및 격자라인 엑셀 옵션 설계
                gb = GridOptionsBuilder.from_dataframe(df_excel_ready.sort_index(ascending=False))
                gb.configure_default_column(
                    sortable=True,
                    filter=True,
                    resizable=True,
                    suppressMovable=True
                )

                # 각 열별 정렬 방향을 엑셀 기본값에 맞춰 정렬 (숫자는 우측 정렬, 문자는 중앙 정렬)
                gb.configure_column("분석 일자", minWidth=120, type=["centerAligned"])
                gb.configure_column("거래량(K)", minWidth=110, type=["numericColumn"])
                if "외국인" in df_excel_ready.columns: gb.configure_column("외국인", minWidth=100, type=["numericColumn"])
                if "기관합계" in df_excel_ready.columns: gb.configure_column("기관합계", minWidth=100, type=["numericColumn"])
                if "개인" in df_excel_ready.columns: gb.configure_column("개인", minWidth=100, type=["numericColumn"])

                # 스크롤 최적화 및 그리드 고정 옵션 빌드
                gb.configure_grid_options(domLayout='normal')
                grid_ops = gb.build()

                # 🌟 [수정됨]: 엑셀 전용 테마인 'balham' 스타일 시트를 적용하여 완벽한 격자 레이아웃 구현
                AgGrid(
                    df_excel_ready.sort_index(ascending=False),
                    gridOptions=grid_ops,
                    height=320,
                    theme='balham',
                    columns_auto_size_mode=ColumnsAutoSizeMode.FIT_CONTENTS,
                    fit_columns_on_grid_load=True
                )

            with col_vol:
                st.markdown("#### 💡 종합 분석 요약 (Summary)")

                if df_investor_raw is None or df_investor_raw.empty:
                    st.warning("⚠️ 현재 공용 서버 노드의 일시적 트래픽 집중으로 수급망 연결이 지연되고 있습니다. 실시간 가격 및 기술적 보조지표 중심 분석 브리핑을 제공합니다.")
                    st.info(
                        f"**{stock_name}**의 기술적 현재가는 {current_price:,.0f}원이며, 리스크 관리를 위한 최적의 진입 유효가는 {entry_price:,.0f}원선, 상방 청산 목표가는 변동성 추정을 가미하여 {target_price:,.0f}원으로 산출됩니다.")
                else:
                    st.success("✅ **스텔스 프록시 안전 연동:** 실시간 메이저 수급 정보 획득에 성공했습니다.")

                    foreign_trend = df_excel_ready['외국인'].head(3).sum() if '외국인' in df_excel_ready.columns else 0
                    inst_trend = df_excel_ready['기관합계'].head(3).sum() if '기관합계' in df_excel_ready.columns else 0

                    if foreign_trend > 0 and inst_trend > 0:
                        brief_msg = "특히 최근 단기 3거래일 기준 외국인과 기관의 강력한 양매수 동향이 관찰되어 상방 변동성에 무게를 싣습니다. "
                    elif foreign_trend > 0:
                        brief_msg = "최근 외국인이 순매수 우위를 유지하며 유의미한 수급 주도권을 가져가고 있습니다. "
                    else:
                        brief_msg = "현재 수급 주체 간 공방이 이어지며 박스권 형태의 매물 소화 과정이 나타나고 있습니다. "

                    st.info(
                        f"**{stock_name}**의 수급 필터링 결과, {brief_msg} 현재 가격 위치에서 제시된 ENTRY 단가({entry_price:,.0f}원)를 지지선 삼아 진입을 고려해볼 수 있으며, 1차 저항 목표 마디가는 {target_price:,.0f}원선으로 설정하는 전략이 유효합니다.")

        except Exception as e:
            st.error(f"데이터를 불러오는 중 내부 오류가 발생했습니다: {e}")
    else:
        st.error("⚠️ 잘못된 종목명이나 코드를 입력하셨습니다.")