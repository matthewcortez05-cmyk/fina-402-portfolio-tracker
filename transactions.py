import sqlite3
import pandas as pd
import os
import time

def get_db():
    conn = sqlite3.connect("investments.db")
    cursor = conn.cursor()
    # Initialize tables once with foreign key support
    cursor.execute("PRAGMA foreign_keys = ON;")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date DATE NOT NULL,
            ticker TEXT NOT NULL,
            buy_sell TEXT NOT NULL,
            shares FLOAT NOT NULL,
            price FLOAT NOT NULL,
            trans_value FLOAT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cash (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date DATE NOT NULL,
            action TEXT NOT NULL,
            amount FLOAT NOT NULL,
            trans_id INTEGER,
            FOREIGN KEY (trans_id) REFERENCES transactions(id) ON DELETE CASCADE
        )
    """)
    conn.commit()
    return conn, cursor

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

while True:
    clear_screen()
    print(" ---- Portfolio Ledger Menu ----\n")
    print("[1] Log a stock/ETF transaction")
    print("[2] Deposit or withdraw cash")
    print("[3] View transaction & cash history")
    print("[4] Edit a transaction")
    print("[5] Exit\n")
    
    menu_select = input("Choose a selection: ").strip()

    # --- 1. Log Stock Transaction ---
    if menu_select == "1":
        date = input("\nEnter date (YYYY-MM-DD): ").strip()
        ticker = input("Enter ticker: ").strip().upper()
        action = input("Buy or sell? (B/S): ").strip().upper()
        shares = abs(float(input("Enter number of shares: ")))
        price = float(input("Enter transaction price: "))

        total_value = round(shares * price, 2)
        conn, cursor = get_db()

        if action == "B":
            buy_sell = "buy"
            trans_shares = shares
            # Buying stocks drains cash (outflow)
            cash_action = "purchase"
            cash_amount = -total_value
        else:
            buy_sell = "sell"
            trans_shares = -shares
            # Selling stocks adds cash (inflow)
            cash_action = "sale"
            cash_amount = total_value

        # 1. Insert Stock Transaction
        cursor.execute("""
            INSERT INTO transactions (date, ticker, buy_sell, shares, price, trans_value) 
            VALUES (?, ?, ?, ?, ?, ?)
        """, (date, ticker, buy_sell, trans_shares, price, total_value))
        
        tx_id = cursor.lastrowid

        # 2. Insert Linked Cash Entry
        cursor.execute("""
            INSERT INTO cash (date, action, amount, trans_id) 
            VALUES (?, ?, ?, ?)
        """, (date, cash_action, cash_amount, tx_id))

        conn.commit()
        conn.close()
        print("\nTransaction and cash adjustment saved!")
        input("Press Enter to continue...")

    # --- 2. Deposit or Withdraw Cash ---
    elif menu_select == "2":
        date = input("\nEnter date (YYYY-MM-DD): ").strip()
        action = input("Deposit or withdraw? (D/W): ").strip().upper()
        amt = abs(float(input("Enter amount: ")))

        if action == "W":
            trans_type = "withdraw"
            cash_amt = -amt
        else:
            trans_type = "deposit"
            cash_amt = amt

        conn, cursor = get_db()
        cursor.execute("""
            INSERT INTO cash (date, action, amount, trans_id) 
            VALUES (?, ?, ?, NULL)
        """, (date, trans_type, cash_amt))

        conn.commit()
        conn.close()
        print("\nCash record logged successfully!")
        input("Press Enter to continue...")

    # --- 3. View History ---
    elif menu_select == "3":
        conn, _ = get_db()
        clear_screen()
        print("=== STOCK TRANSACTIONS ===")
        df_tx = pd.read_sql_query("""
            SELECT id, date, ticker, buy_sell, shares, price, trans_value,
                   SUM(shares) OVER (PARTITION BY ticker ORDER BY date, id) AS pos_shares
            FROM transactions 
            ORDER BY date ASC, id ASC
        """, conn)
        print(df_tx.to_string(index=False) if not df_tx.empty else "No transactions logged.")

        print("\n=== CASH LEDGER ===")
        df_cash = pd.read_sql_query("""
            SELECT id, date, action, amount, trans_id,
                   SUM(amount) OVER (ORDER BY date, id) AS running_cash_balance
            FROM cash 
            ORDER BY date ASC, id ASC
        """, conn)
        print(df_cash.to_string(index=False) if not df_cash.empty else "No cash movements logged.")
        
        conn.close()
        input("\nPress Enter to return to menu...")

    # --- 4. Edit a Transaction ---
    elif menu_select == "4":
        conn, cursor = get_db()
        df = pd.read_sql_query("SELECT id, date, ticker, buy_sell, shares, price, trans_value FROM transactions", conn)
        
        if df.empty:
            print("\nNo transactions available to edit.")
            conn.close()
            time.sleep(1.5)
            continue

        print(f"\n{df.to_string(index=False)}")
        target_id = input("\nEnter transaction ID to edit: ").strip()

        new_date = input("Enter new date (YYYY-MM-DD): ").strip()
        new_ticker = input("Enter ticker: ").strip().upper()
        new_action = input("Buy or sell? (B/S): ").strip().upper()
        new_shares = abs(float(input("Enter number of shares: ")))
        new_price = float(input("Enter price: "))

        new_total_val = round(new_shares * new_price, 2)

        if new_action == "B":
            new_buy_sell = "buy"
            trans_shares = new_shares
            new_cash_action = "purchase"
            new_cash_amt = -new_total_val
        else:
            new_buy_sell = "sell"
            trans_shares = -new_shares
            new_cash_action = "sale"
            new_cash_amt = new_total_val

        # Update Stock Record
        cursor.execute("""
            UPDATE transactions
            SET date = ?, ticker = ?, buy_sell = ?, shares = ?, price = ?, trans_value = ?
            WHERE id = ?
        """, (new_date, new_ticker, new_buy_sell, trans_shares, new_price, new_total_val, target_id))

        # Update Linked Cash Entry
        cursor.execute("""
            UPDATE cash
            SET date = ?, action = ?, amount = ?
            WHERE trans_id = ?
        """, (new_date, new_cash_action, new_cash_amt, target_id))

        conn.commit()
        conn.close()
        print("\nTransaction and linked cash ledger updated!")
        input("Press Enter to continue...")

    # --- 5. Exit ---
    elif menu_select == "5":
        print("Exiting...")
        time.sleep(1)
        break