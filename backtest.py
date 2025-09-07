#!/usr/bin/env python3
"""
Kripto Trading Bot Backtest Script
Historical data ile stratejiyi test eder
"""

import pandas as pd
import numpy as np
import talib
import ccxt
import os
import csv
from datetime import datetime, timedelta
from dotenv import load_dotenv
import json
from typing import Dict, List, Tuple

load_dotenv()

class CryptoBacktest:
    def __init__(self):
        # Backtest parametreleri
        self.symbols = ['BTC/USDT:USDT', 'ETH/USDT:USDT']
        self.timeframe = '1h'
        self.initial_balance = 1000  # Starting capital
        self.leverage = 3
        self.risk_per_trade = 0.01  # %1 risk per trade
        
        # Strategy parameters
        self.rsi_oversold = 30
        self.rsi_overbought = 60
        self.volume_multiplier = 1.8
        self.tp_percent = 0.06  # %6 TP
        self.sl_percent = 0.03  # %3 SL
        self.trailing_trigger = 0.03
        self.trailing_distance = 0.015
        
        # Risk management
        self.max_consecutive_losses = 3
        self.commission_rate = 0.0004  # %0.04 taker fee
        
        # Results storage
        self.trades = []
        self.equity_curve = []
        self.stats = {}
        
        # Setup exchange for data fetching
        self.setup_exchange()
        
    def setup_exchange(self):
        """Exchange setup for data fetching"""
        self.exchange = ccxt.bybit({
            'apiKey': os.getenv('BYBIT_API_KEY', ''),
            'secret': os.getenv('BYBIT_API_SECRET', ''),
            'sandbox': True,
            'enableRateLimit': True,
        })
        
    def fetch_historical_data(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Historical OHLCV data çek"""
        print(f"📊 Fetching historical data for {symbol}...")
        
        try:
            # Convert dates to timestamps
            start_ts = int(pd.Timestamp(start_date).timestamp() * 1000)
            end_ts = int(pd.Timestamp(end_date).timestamp() * 1000)
            
            all_data = []
            current_ts = start_ts
            
            while current_ts < end_ts:
                try:
                    data = self.exchange.fetch_ohlcv(
                        symbol, self.timeframe, 
                        since=current_ts, 
                        limit=1000
                    )
                    
                    if not data:
                        break
                        
                    all_data.extend(data)
                    current_ts = data[-1][0] + 3600000  # +1 hour
                    
                    print(f"📈 Loaded {len(all_data)} candles for {symbol}")
                    
                except Exception as e:
                    print(f"⚠️ Data fetch error: {e}")
                    break
                    
            # Convert to DataFrame
            df = pd.DataFrame(all_data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df = df.drop_duplicates(subset=['timestamp']).reset_index(drop=True)
            
            print(f"✅ {symbol} data loaded: {len(df)} candles from {df['timestamp'].min()} to {df['timestamp'].max()}")
            return df
            
        except Exception as e:
            print(f"❌ Historical data fetch failed for {symbol}: {e}")
            return pd.DataFrame()
    
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Technical indicators hesapla"""
        # RSI
        df['rsi'] = talib.RSI(df['close'].values, timeperiod=14)
        
        # Volume ratio (20-period moving average)
        df['volume_ma20'] = df['volume'].rolling(window=20).mean()
        df['volume_ratio'] = df['volume'] / df['volume_ma20']
        
        # ATR for volatility
        df['atr'] = talib.ATR(df['high'].values, df['low'].values, df['close'].values, timeperiod=14)
        
        return df
        
    def check_entry_signal(self, row: pd.Series) -> bool:
        """Entry signal kontrolü"""
        rsi_condition = row['rsi'] < self.rsi_oversold
        volume_condition = row['volume_ratio'] > self.volume_multiplier
        
        return rsi_condition and volume_condition and not pd.isna(row['rsi'])
        
    def check_exit_signal(self, row: pd.Series, entry_price: float, highest_price: float) -> Tuple[bool, str, float]:
        """Exit signal kontrolü"""
        current_price = row['close']
        
        # RSI exit
        if row['rsi'] > self.rsi_overbought:
            return True, "RSI_EXIT", current_price
            
        # Take Profit
        profit_percent = (current_price - entry_price) / entry_price
        if profit_percent >= self.tp_percent:
            return True, "TP", entry_price * (1 + self.tp_percent)
            
        # Stop Loss
        if profit_percent <= -self.sl_percent:
            return True, "SL", entry_price * (1 - self.sl_percent)
            
        # Trailing Stop
        if profit_percent >= self.trailing_trigger:
            trailing_stop = highest_price * (1 - self.trailing_distance)
            if current_price <= trailing_stop:
                return True, "TRAILING_SL", trailing_stop
                
        return False, "", current_price
        
    def calculate_position_size(self, balance: float, entry_price: float) -> float:
        """Position size hesapla"""
        risk_amount = balance * self.risk_per_trade
        stop_distance = entry_price * self.sl_percent
        
        position_size_usdt = risk_amount / (stop_distance / entry_price)
        position_size_base = position_size_usdt / entry_price
        
        # Leverage apply
        leveraged_position = position_size_base * self.leverage
        
        return leveraged_position
        
    def run_backtest_symbol(self, symbol: str, df: pd.DataFrame) -> List[Dict]:
        """Tek symbol için backtest çalıştır"""
        print(f"🔄 Running backtest for {symbol}...")
        
        trades = []
        balance = self.initial_balance
        in_position = False
        entry_price = 0
        entry_time = None
        position_size = 0
        highest_price = 0
        consecutive_losses = 0
        paused_until = None
        
        for i, row in df.iterrows():
            current_time = row['timestamp']
            current_price = row['close']
            
            # Check if paused
            if paused_until and current_time < paused_until:
                continue
            elif paused_until and current_time >= paused_until:
                paused_until = None
                consecutive_losses = 0
                
            # Skip if no RSI data
            if pd.isna(row['rsi']):
                continue
                
            if not in_position:
                # Check entry signal
                if self.check_entry_signal(row):
                    entry_price = current_price
                    entry_time = current_time
                    position_size = self.calculate_position_size(balance, entry_price)
                    highest_price = entry_price
                    in_position = True
                    
            else:
                # Update highest price for trailing
                if current_price > highest_price:
                    highest_price = current_price
                    
                # Check exit signal
                should_exit, exit_reason, exit_price = self.check_exit_signal(
                    row, entry_price, highest_price
                )
                
                if should_exit:
                    # Calculate PnL
                    pnl_percent = (exit_price - entry_price) / entry_price
                    pnl_usdt = pnl_percent * position_size * entry_price
                    
                    # Commission
                    commission = (entry_price + exit_price) * position_size * self.commission_rate
                    net_pnl = pnl_usdt - commission
                    
                    # Update balance
                    balance += net_pnl
                    
                    # Track consecutive losses
                    if net_pnl < 0:
                        consecutive_losses += 1
                    else:
                        consecutive_losses = 0
                        
                    # Pause if too many losses
                    if consecutive_losses >= self.max_consecutive_losses:
                        paused_until = current_time + timedelta(hours=24)
                        
                    # Record trade
                    trade = {
                        'timestamp': current_time,
                        'symbol': symbol,
                        'entry_price': entry_price,
                        'exit_price': exit_price,
                        'quantity': position_size,
                        'pnl_usdt': round(net_pnl, 2),
                        'pnl_percent': round(pnl_percent * 100, 2),
                        'reason': exit_reason,
                        'rsi_entry': df.loc[df['timestamp'] == entry_time, 'rsi'].iloc[0],
                        'volume_ratio': df.loc[df['timestamp'] == entry_time, 'volume_ratio'].iloc[0],
                        'holding_hours': int((current_time - entry_time).total_seconds() / 3600),
                        'commission_usdt': round(commission, 2),
                        'balance': round(balance, 2)
                    }
                    
                    trades.append(trade)
                    in_position = False
                    
        print(f"✅ {symbol} backtest completed: {len(trades)} trades")
        return trades
        
    def run_full_backtest(self, start_date: str = "2023-01-01", end_date: str = "2024-05-01"):
        """Full backtest çalıştır"""
        print(f"🚀 Starting backtest from {start_date} to {end_date}")
        
        all_trades = []
        
        for symbol in self.symbols:
            # Fetch data
            df = self.fetch_historical_data(symbol, start_date, end_date)
            if df.empty:
                continue
                
            # Calculate indicators
            df = self.calculate_indicators(df)
            
            # Run backtest
            symbol_trades = self.run_backtest_symbol(symbol, df)
            all_trades.extend(symbol_trades)
            
        # Sort trades by timestamp
        all_trades.sort(key=lambda x: x['timestamp'])
        
        self.trades = all_trades
        self.calculate_performance_stats()
        self.save_results()
        
    def calculate_performance_stats(self):
        """Performance istatistikleri hesapla"""
        if not self.trades:
            print("❌ No trades to analyze")
            return
            
        df_trades = pd.DataFrame(self.trades)
        
        # Basic stats
        total_trades = len(df_trades)
        winning_trades = len(df_trades[df_trades['pnl_usdt'] > 0])
        losing_trades = total_trades - winning_trades
        win_rate = winning_trades / total_trades if total_trades > 0 else 0
        
        # PnL stats
        total_pnl = df_trades['pnl_usdt'].sum()
        avg_win = df_trades[df_trades['pnl_usdt'] > 0]['pnl_usdt'].mean() if winning_trades > 0 else 0
        avg_loss = df_trades[df_trades['pnl_usdt'] < 0]['pnl_usdt'].mean() if losing_trades > 0 else 0
        
        # Risk metrics
        returns = df_trades['pnl_usdt'] / self.initial_balance
        sharpe_ratio = returns.mean() / returns.std() * np.sqrt(365*24) if returns.std() > 0 else 0
        
        # Drawdown calculation
        df_trades['cumulative_pnl'] = df_trades['pnl_usdt'].cumsum()
        df_trades['peak'] = df_trades['cumulative_pnl'].cummax()
        df_trades['drawdown'] = (df_trades['cumulative_pnl'] - df_trades['peak']) / self.initial_balance
        max_drawdown = df_trades['drawdown'].min()
        
        # Consecutive losses
        consecutive_losses = []
        current_streak = 0
        for pnl in df_trades['pnl_usdt']:
            if pnl < 0:
                current_streak += 1
            else:
                if current_streak > 0:
                    consecutive_losses.append(current_streak)
                current_streak = 0
        if current_streak > 0:
            consecutive_losses.append(current_streak)
            
        max_consecutive_losses = max(consecutive_losses) if consecutive_losses else 0
        
        # Profit factor
        gross_profit = df_trades[df_trades['pnl_usdt'] > 0]['pnl_usdt'].sum()
        gross_loss = abs(df_trades[df_trades['pnl_usdt'] < 0]['pnl_usdt'].sum())
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        
        self.stats = {
            'total_trades': total_trades,
            'winning_trades': winning_trades,
            'losing_trades': losing_trades,
            'win_rate': round(win_rate, 3),
            'total_pnl': round(total_pnl, 2),
            'total_return': round(total_pnl / self.initial_balance, 3),
            'avg_win': round(avg_win, 2),
            'avg_loss': round(avg_loss, 2),
            'profit_factor': round(profit_factor, 2),
            'sharpe_ratio': round(sharpe_ratio, 2),
            'max_drawdown': round(abs(max_drawdown), 3),
            'max_consecutive_losses': max_consecutive_losses,
            'avg_holding_time': round(df_trades['holding_hours'].mean(), 1),
            'final_balance': round(self.initial_balance + total_pnl, 2)
        }
        
        self.print_results()
        
    def print_results(self):
        """Results'ı yazdır"""
        print("\n" + "="*60)
        print("📊 BACKTEST RESULTS")
        print("="*60)
        
        print(f"📈 Total Trades: {self.stats['total_trades']}")
        print(f"🎯 Win Rate: {self.stats['win_rate']:.1%}")
        print(f"💰 Total PnL: ${self.stats['total_pnl']}")
        print(f"📊 Total Return: {self.stats['total_return']:.1%}")
        print(f"💵 Final Balance: ${self.stats['final_balance']}")
        
        print(f"\n🔍 PERFORMANCE METRICS:")
        print(f"   Profit Factor: {self.stats['profit_factor']}")
        print(f"   Sharpe Ratio: {self.stats['sharpe_ratio']}")
        print(f"   Max Drawdown: {self.stats['max_drawdown']:.1%}")
        print(f"   Max Consecutive Losses: {self.stats['max_consecutive_losses']}")
        print(f"   Avg Holding Time: {self.stats['avg_holding_time']} hours")
        
        print(f"\n💹 TRADE ANALYSIS:")
        print(f"   Winning Trades: {self.stats['winning_trades']}")
        print(f"   Losing Trades: {self.stats['losing_trades']}")
        print(f"   Avg Win: ${self.stats['avg_win']}")
        print(f"   Avg Loss: ${self.stats['avg_loss']}")
        
        # Success criteria check
        print(f"\n✅ SUCCESS CRITERIA CHECK:")
        criteria = {
            "Win Rate ≥ 55%": self.stats['win_rate'] >= 0.55,
            "Max Drawdown ≤ 20%": self.stats['max_drawdown'] <= 0.20,
            "Min 100 Trades": self.stats['total_trades'] >= 100,
            "Profit Factor ≥ 1.3": self.stats['profit_factor'] >= 1.3,
            "Positive Return": self.stats['total_return'] > 0
        }
        
        for criteria_name, passed in criteria.items():
            status = "✅" if passed else "❌"
            print(f"   {status} {criteria_name}: {passed}")
            
        all_passed = all(criteria.values())
        print(f"\n{'✅ STRATEGY APPROVED FOR LIVE TRADING' if all_passed else '❌ STRATEGY NEEDS OPTIMIZATION'}")
        
    def save_results(self):
        """Results'ı dosyalara kaydet"""
        os.makedirs('logs', exist_ok=True)
        
        # Save detailed trades
        trades_file = 'logs/backtest_results.csv'
        with open(trades_file, 'w', newline='') as f:
            if self.trades:
                writer = csv.DictWriter(f, fieldnames=self.trades[0].keys())
                writer.writeheader()
                writer.writerows(self.trades)
                
        # Save summary stats
        stats_file = 'logs/backtest_summary.json'
        with open(stats_file, 'w') as f:
            json.dump(self.stats, f, indent=2, default=str)
            
        print(f"\n💾 Results saved:")
        print(f"   📊 Detailed trades: {trades_file}")
        print(f"   📈 Summary stats: {stats_file}")


def main():
    """Ana fonksiyon"""
    print("🚀 Crypto Trading Bot - Backtest")
    print("="*40)
    
    backtest = CryptoBacktest()
    
    # Backtest parametrelerini göster
    print(f"💡 Configuration:")
    print(f"   Symbols: {', '.join(backtest.symbols)}")
    print(f"   Initial Balance: ${backtest.initial_balance}")
    print(f"   Risk per Trade: {backtest.risk_per_trade:.1%}")
    print(f"   Leverage: {backtest.leverage}x")
    print(f"   RSI: {backtest.rsi_oversold}/{backtest.rsi_overbought}")
    print(f"   Volume Multiplier: {backtest.volume_multiplier}x")
    print(f"   TP/SL: {backtest.tp_percent:.1%}/{backtest.sl_percent:.1%}")
    
    # Run backtest
    try:
        backtest.run_full_backtest()
    except KeyboardInterrupt:
        print("\n🛑 Backtest interrupted by user")
    except Exception as e:
        print(f"\n❌ Backtest failed: {e}")


if __name__ == "__main__":
    main()