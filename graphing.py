import sqlite3
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import date, timedelta
from flask import Flask, render_template, jsonify

app = Flask(__name__, template_folder='templates')

def sync_daily_valuations():
    conn = sqlite3.connect('investments.db')
    
    # 1. Fetch cash records
    try:
        df_cash = pd.read_sql_query("SELECT date, amount FROM cash", conn)
    except Exception:
        df_cash = pd.DataFrame()

    # 2. Fetch stock transactions
    try:
        query = """
            SELECT
                date,
                ticker,
                SUM(shares) OVER (PARTITION BY ticker ORDER BY date, id) AS cum_shares,
                SUM(trans_value) OVER (PARTITION BY ticker ORDER BY date, id) AS net_invested
            FROM transactions
            ORDER BY date ASC
        """
        df_tx = pd.read_sql_query(query, conn)
    except Exception:
        df_tx = pd.DataFrame()

    # Guard: Return early only if BOTH tables are empty
    if df_cash.empty and df_tx.empty:
        print("[Notice] No transactions or cash records found in investments.db.")
        conn.close()
        return False

    # -------------------------------------------------------------
    # CASE A: 100% Cash Portfolio (No stock purchases logged yet)
    # -------------------------------------------------------------
    if df_tx.empty and not df_cash.empty:
        df_cash['date'] = pd.to_datetime(df_cash['date'])
        daily_cash = df_cash.groupby('date')['amount'].sum().sort_index()

        start_dt = daily_cash.index.min()
        end_dt = pd.to_datetime(date.today())
        full_cal = pd.date_range(start=start_dt, end=end_dt, freq='D')
        rolling_cash = daily_cash.reindex(full_cal, fill_value=0).cumsum()

        cash_rows = []
        for dt in full_cal:
            d_str = dt.strftime('%Y-%m-%d')
            bal = round(float(rolling_cash.asof(dt) or 0.0), 2)
            cash_rows.append({
                'date': d_str,
                'ticker': 'CASH',
                'close': 1.0,
                'cum_shares': bal,
                'net_invested': bal,
                'market_value': bal,
                'unrealized_gain': 0.0,
                'return_pct': 0.0,
                'sector': 'Cash'
            })

        history = pd.DataFrame(cash_rows)
        history.to_sql("daily_valuations", conn, if_exists="replace", index=False)
        cursor = conn.cursor()
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_val_date_ticker ON daily_valuations(date, ticker);")
        conn.commit()
        conn.close()
        print("[Success] Cash-only valuations synced to investments.db")
        return True

    # -------------------------------------------------------------
    # CASE B: Portfolio Has Stock Positions (+ Cash)
    # -------------------------------------------------------------
    df_tx['date'] = pd.to_datetime(df_tx['date'])
    tx_daily = df_tx.groupby(['date', 'ticker'])[['cum_shares', 'net_invested']].last().reset_index()

    tickers = tx_daily['ticker'].unique().tolist()
    
    # Start date covers either the earliest cash deposit or stock trade
    min_date_candidate = tx_daily['date'].min()
    if not df_cash.empty:
        df_cash['date'] = pd.to_datetime(df_cash['date'])
        min_date_candidate = min(min_date_candidate, df_cash['date'].min())

    start_date = min_date_candidate.strftime('%Y-%m-%d')
    end_date = (date.today() + timedelta(days=1)).strftime('%Y-%m-%d')

    print(f"Fetching market data for {tickers} from {start_date} to {end_date}...")
    try:
        raw_download = yf.download(tickers, start=start_date, end=end_date, progress=False)
        if raw_download.empty or 'Close' not in raw_download:
            print("[Warning] No market price data returned.")
            conn.close()
            return False
        price_data = raw_download['Close']
        
        sector_map = {}
        for t in tickers:
            try:
                info = yf.Ticker(t).info
                quote_type = info.get("quoteType", "")
                if quote_type == "ETF":
                    sector_map[t] = info.get("category") or "ETFs & Funds"
                else:
                    sector_map[t] = info.get("sector") or "Other"
            except Exception:
                sector_map[t] = "Other"

    except Exception as e:
        print(f"[Error] Yahoo Finance download failed: {e}")
        conn.close()
        return False

    if isinstance(price_data, pd.Series):
        prices = price_data.reset_index()
        prices.columns = ['date', 'close']
        prices['ticker'] = tickers[0]
    elif isinstance(price_data, pd.DataFrame):
        if len(tickers) == 1:
            prices = price_data.reset_index()
            prices.columns = ['date', 'close']
            prices['ticker'] = tickers[0]
        else:
            prices = price_data.stack().reset_index()
            prices.columns = ['date', 'ticker', 'close']
    else:
        conn.close()
        return False

    prices['date'] = pd.to_datetime(prices['date'])
    history = pd.merge(prices, tx_daily, on=['date', 'ticker'], how='left')

    history['cum_shares'] = history.groupby('ticker')['cum_shares'].ffill().fillna(0)
    history['net_invested'] = history.groupby('ticker')['net_invested'].ffill().fillna(0)
    history = history[history['cum_shares'] > 0].copy()

    if not history.empty:
        history['market_value'] = (history['cum_shares'] * history['close']).round(2)
        history['unrealized_gain'] = (history['market_value'] - history['net_invested']).round(2)
        history['return_pct'] = ((history['unrealized_gain'] / history['net_invested']) * 100).round(2)
        history['sector'] = history['ticker'].map(sector_map).fillna("Other")
        history['date'] = history['date'].dt.strftime('%Y-%m-%d')

    # Append rolling cash across trading dates
    if not df_cash.empty:
        daily_cash = df_cash.groupby('date')['amount'].sum().sort_index()
        trading_dates = sorted(prices['date'].dt.strftime('%Y-%m-%d').unique())

        start_cal = min(daily_cash.index.min(), pd.to_datetime(trading_dates[0]))
        end_cal = max(daily_cash.index.max(), pd.to_datetime(trading_dates[-1]))
        full_calendar = pd.date_range(start=start_cal, end=end_cal, freq='D')

        rolling_cash = daily_cash.reindex(full_calendar, fill_value=0).cumsum()

        cash_rows = []
        for d_str in trading_dates:
            dt = pd.to_datetime(d_str)
            bal = round(float(rolling_cash.asof(dt) or 0.0), 2)
            cash_rows.append({
                'date': d_str,
                'ticker': 'CASH',
                'close': 1.0,
                'cum_shares': bal,
                'net_invested': bal,
                'market_value': bal,
                'unrealized_gain': 0.0,
                'return_pct': 0.0,
                'sector': 'Cash'
            })

        df_cash_history = pd.DataFrame(cash_rows)
        history = pd.concat([history, df_cash_history], ignore_index=True)

    history = history.sort_values(by=['ticker', 'date']).reset_index(drop=True)
    history.to_sql("daily_valuations", conn, if_exists="replace", index=False)
    cursor = conn.cursor()
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_val_date_ticker ON daily_valuations(date, ticker);")
    conn.commit()
    conn.close()
    print("[Success] Daily valuations synced to investments.db")
    return True

# --- Flask Routes ---

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/history', methods=['GET'])
def get_history():
    conn = sqlite3.connect('investments.db')
    try:
        df = pd.read_sql_query("SELECT * FROM daily_valuations ORDER BY date ASC", conn)
        conn.close()
        return jsonify(df.to_dict(orient='records'))
    except Exception:
        conn.close()
        return jsonify([])

@app.route('/api/metrics', methods=['GET'])
def get_metrics():
    conn = sqlite3.connect('investments.db')
    try:
        df_val = pd.read_sql_query("""
            SELECT ticker, market_value 
            FROM daily_valuations 
            WHERE date = (SELECT MAX(date) FROM daily_valuations)
              AND ticker != 'CASH'
        """, conn)

        df_history = pd.read_sql_query("""
            SELECT date, SUM(market_value) as total_mkt_val, SUM(net_invested) as total_cost
            FROM daily_valuations
            GROUP BY date
            ORDER BY date ASC
        """, conn)
        conn.close()

        if df_history.empty:
            return jsonify({})

        # Total Return & Drawdown
        df_history['peak'] = df_history['total_mkt_val'].cummax()
        df_history['drawdown'] = (df_history['total_mkt_val'] - df_history['peak']) / df_history['peak']
        max_drawdown = float(df_history['drawdown'].min() * 100)

        latest_mkt = df_history['total_mkt_val'].iloc[-1]
        latest_cost = df_history['total_cost'].iloc[-1]
        port_total_return = ((latest_mkt - latest_cost) / latest_cost * 100) if latest_cost > 0 else 0.0

        # SPY Return
        start_date_str = df_history['date'].min()
        end_date_str = (date.today() + timedelta(days=1)).strftime('%Y-%m-%d')
        spy_raw = yf.download('SPY', start=start_date_str, end=end_date_str, progress=False)
        spy_total_return = 0.0
        if not spy_raw.empty and 'Close' in spy_raw:
            spy_closes = spy_raw['Close'].dropna()
            if len(spy_closes) >= 2:
                spy_total_return = float(((spy_closes.iloc[-1] - spy_closes.iloc[0]) / spy_closes.iloc[0]) * 100)

        # If holding 100% cash, return clean 0.0 baseline metrics
        if df_val.empty:
            return jsonify({
                "port_total_return": round(float(port_total_return), 2),
                "spy_total_return": round(float(spy_total_return), 2),
                "sharpe": 0.0,
                "alpha": 0.0,
                "beta": 0.0,
                "information_ratio": 0.0,
                "max_drawdown": round(float(max_drawdown), 2)
            })

        total_val = df_val['market_value'].sum()
        df_val['weight'] = df_val['market_value'] / total_val
        tickers = df_val['ticker'].tolist()
        download_list = list(set(tickers + ['SPY']))

        raw_2y = yf.download(download_list, period="2y", interval="1wk", progress=False)
        if raw_2y.empty or 'Close' not in raw_2y:
            return jsonify({
                "port_total_return": round(port_total_return, 2),
                "spy_total_return": round(spy_total_return, 2),
                "max_drawdown": round(max_drawdown, 2),
                "sharpe": 0.0, "alpha": 0.0, "beta": 0.0, "information_ratio": 0.0
            })

        closes = raw_2y['Close']
        weekly_returns = closes.pct_change().dropna()

        if 'SPY' not in weekly_returns.columns:
            return jsonify({})

        bench_returns = weekly_returns['SPY']

        available_tickers = [t for t in tickers if t in weekly_returns.columns]
        weights = df_val.set_index('ticker')['weight'].reindex(available_tickers).fillna(0)
        if weights.sum() > 0:
            weights = weights / weights.sum()

        port_returns = weekly_returns[available_tickers].dot(weights)

        common_idx = port_returns.index.intersection(bench_returns.index)
        port_returns = port_returns.loc[common_idx]
        bench_returns = bench_returns.loc[common_idx]

        rf_annual = 0.04
        rf_weekly = (1 + rf_annual) ** (1 / 52) - 1

        mean_p = port_returns.mean()
        std_p = port_returns.std()
        sharpe = ((mean_p - rf_weekly) / std_p) * (52 ** 0.5) if std_p > 0 else 0.0

        var_bench = bench_returns.var()
        cov_p_b = port_returns.cov(bench_returns)
        beta = cov_p_b / var_bench if var_bench > 0 else 1.0

        mean_b = bench_returns.mean()
        alpha_weekly = (mean_p - rf_weekly) - (beta * (mean_b - rf_weekly))
        alpha_annual = alpha_weekly * 52 * 100

        active_diff = port_returns - bench_returns
        tracking_error_annual = active_diff.std() * (52 ** 0.5)
        ir = (active_diff.mean() * 52) / tracking_error_annual if tracking_error_annual > 0 else 0.0

        return jsonify({
            "port_total_return": round(float(port_total_return), 2),
            "spy_total_return": round(float(spy_total_return), 2),
            "sharpe": round(float(sharpe), 2),
            "alpha": round(float(alpha_annual), 2),
            "beta": round(float(beta), 2),
            "information_ratio": round(float(ir), 2),
            "max_drawdown": round(float(max_drawdown), 2)
        })

    except Exception as e:
        print(f"[Error] Failed to calculate metrics: {e}")
        if 'conn' in locals():
            conn.close()
        return jsonify({})

@app.route('/api/sync', methods=['POST'])
def trigger_sync():
    success = sync_daily_valuations()
    if success:
        return jsonify({"status": "success", "message": "Valuations updated"}), 200
    return jsonify({"status": "error", "message": "Failed to update valuations"}), 400

sync_daily_valuations()

if __name__ == "__main__":
    app.run(debug=True, port=8000)
