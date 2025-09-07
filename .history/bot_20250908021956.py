#!/usr/bin/env python3
"""
Kripto Algoritmik Trading Bot - Ana Dosya
RSI + Volume Stratejisi ile BTC/USDT ve ETH/USDT 3x Leverage Trading
"""

import ccxt
import pandas as pd
import numpy as np
import talib
import time
import logging
import os
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv
import schedule
import csv
from typing import Dict, List, Optional, Tuple

# .env dosyasını yükle
load_dotenv()

class CryptoTradingBot:
    def __init__(self):
        # API Konfigürasyonu
        self.api_key = os.getenv('BYBIT_API_KEY')
        self.api_secret = os.getenv('BYBIT_API_SECRET')
        self.testnet = os.getenv('TESTNET', 'true').lower() == 'true'
        
        # Bot parametreleri
        self.symbols = ['BTC/USDT:USDT', 'ETH/USDT:USDT']
        self.leverage = 3
        self.risk_per_trade = 0.01  # %1 risk
        self.rsi_oversold = 30
        self.rsi_overbought = 60
        self.volume_multiplier = 1.8
        self.tp_percent = 0.06  # %6 TP
        self.sl_percent = 0.03  # %3 SL
        self.trailing_trigger = 0.03  # %3 kar geçince trailing başla
        self.trailing_distance = 0.015  # %1.5 trailing distance
        
        # Risk yönetimi
        self.max_consecutive_losses = 3
        self.pause_hours = 24
        self.consecutive_losses = 0
        self.paused_until = None
        
  # Logging setup
        self.setup_logging()
        # Exchange setup
        self.setup_exchange()
        
      
     
        
        # CSV log setup
        self.setup_csv_logging()
        
        # State variables
        self.open_positions = {}
        self.last_trades = []
        
    def setup_exchange(self):
        """Exchange connection kurulumu"""
        try:
            self.exchange = ccxt.bybit({
                'apiKey': self.api_key,
                'secret': self.api_secret,
                'sandbox': self.testnet,  # Testnet modu
                'enableRateLimit': True,
                'options': {
                    'defaultType': 'linear',  # USDT futures
                    'recvWindow': 10000,
                }
            })
            
            # Test connection
            balance = self.exchange.fetch_balance()
            self.logger.info(f"✅ Bybit bağlantısı başarılı - Bakiye: {balance['USDT']['free']:.2f} USDT")
            
        except Exception as e:
            self.logger.error(f"❌ Exchange bağlantısı başarısız: {e}")
            raise e
            
    def setup_logging(self):
        """Logging sistemini kur"""
        os.makedirs('logs', exist_ok=True)
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s [%(levelname)s] %(message)s',
            handlers=[
                logging.FileHandler('logs/system.log'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
        
    def setup_csv_logging(self):
        """CSV log dosyasını hazırla"""
        self.csv_file = 'logs/trades.csv'
        
        # Header oluştur eğer dosya yoksa
        if not os.path.exists(self.csv_file):
            headers = [
                'timestamp', 'symbol', 'side', 'entry_price', 'exit_price',
                'quantity', 'pnl_usdt', 'pnl_percent', 'reason', 'rsi_entry',
                'volume_ratio', 'funding_cost', 'commission_usdt'
            ]
            
            with open(self.csv_file, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(headers)
                
    def log_trade(self, trade_data: Dict):
        """Trade'i CSV'ye logla"""
        with open(self.csv_file, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                trade_data['timestamp'],
                trade_data['symbol'],
                trade_data['side'],
                trade_data['entry_price'],
                trade_data['exit_price'],
                trade_data['quantity'],
                trade_data['pnl_usdt'],
                trade_data['pnl_percent'],
                trade_data['reason'],
                trade_data['rsi_entry'],
                trade_data['volume_ratio'],
                trade_data.get('funding_cost', 0),
                trade_data.get('commission_usdt', 0)
            ])
            
    def fetch_ohlcv_data(self, symbol: str, timeframe: str = '1h', limit: int = 100) -> pd.DataFrame:
        """OHLCV verilerini çek"""
        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            return df
            
        except Exception as e:
            self.logger.error(f"❌ {symbol} OHLCV data fetch error: {e}")
            return pd.DataFrame()
            
    def calculate_rsi(self, prices: pd.Series, period: int = 14) -> pd.Series:
        """RSI hesapla"""
        try:
            rsi = talib.RSI(prices.values, timeperiod=period)
            return pd.Series(rsi, index=prices.index)
        except Exception as e:
            self.logger.error(f"RSI calculation error: {e}")
            return pd.Series()
            
    def calculate_volume_ratio(self, volumes: pd.Series, period: int = 20) -> float:
        """Hacim oranını hesapla"""
        try:
            avg_volume = volumes.iloc[-period:].mean()
            current_volume = volumes.iloc[-1]
            return current_volume / avg_volume
        except Exception as e:
            self.logger.error(f"Volume ratio calculation error: {e}")
            return 0
            
    def get_account_balance(self) -> float:
        """USDT bakiyesini al"""
        try:
            balance = self.exchange.fetch_balance()
            return float(balance['USDT']['free'])
        except Exception as e:
            self.logger.error(f"Balance fetch error: {e}")
            return 0
            
    def calculate_position_size(self, symbol: str, entry_price: float, stop_price: float) -> float:
        """Pozisyon büyüklüğünü hesapla"""
        try:
            balance = self.get_account_balance()
            risk_amount = balance * self.risk_per_trade
            
            # Stop distance
            stop_distance = abs(entry_price - stop_price)
            
            # Position size (leverage dahil)
            position_size_usdt = risk_amount / (stop_distance / entry_price)
            position_size_base = position_size_usdt / entry_price
            
            # Leverage uygula
            leveraged_position = position_size_base * self.leverage
            
            # Minimum position size kontrolü
            min_size = 0.001 if 'BTC' in symbol else 0.01
            leveraged_position = max(leveraged_position, min_size)
            
            self.logger.info(f"Position size calculated: {leveraged_position:.6f} for {symbol}")
            return leveraged_position
            
        except Exception as e:
            self.logger.error(f"Position size calculation error: {e}")
            return 0
            
    def check_entry_conditions(self, symbol: str) -> Tuple[bool, Dict]:
        """Entry koşullarını kontrol et"""
        try:
            # OHLCV verilerini al
            df = self.fetch_ohlcv_data(symbol, '1h', 100)
            if df.empty:
                return False, {}
                
            # RSI hesapla
            rsi_series = self.calculate_rsi(df['close'])
            current_rsi = rsi_series.iloc[-1]
            
            # Volume ratio hesapla
            volume_ratio = self.calculate_volume_ratio(df['volume'])
            
            # Entry conditions
            rsi_condition = current_rsi < self.rsi_oversold
            volume_condition = volume_ratio > self.volume_multiplier
            
            conditions = {
                'rsi': current_rsi,
                'volume_ratio': volume_ratio,
                'rsi_condition': rsi_condition,
                'volume_condition': volume_condition,
                'current_price': df['close'].iloc[-1]
            }
            
            entry_signal = rsi_condition and volume_condition
            
            if entry_signal:
                self.logger.info(f"🟢 {symbol} Entry signal: RSI={current_rsi:.1f}, Volume Ratio={volume_ratio:.1f}")
            else:
                self.logger.debug(f"⚪ {symbol} No entry: RSI={current_rsi:.1f}, Volume Ratio={volume_ratio:.1f}")
                
            return entry_signal, conditions
            
        except Exception as e:
            self.logger.error(f"Entry condition check error for {symbol}: {e}")
            return False, {}
            
    def open_position(self, symbol: str, conditions: Dict) -> bool:
        """Long pozisyon aç"""
        try:
            current_price = conditions['current_price']
            
            # Stop loss ve take profit hesapla
            stop_loss = current_price * (1 - self.sl_percent)
            take_profit = current_price * (1 + self.tp_percent)
            
            # Position size hesapla
            position_size = self.calculate_position_size(symbol, current_price, stop_loss)
            
            if position_size <= 0:
                self.logger.error(f"Invalid position size for {symbol}")
                return False
                
            # Leverage ayarla
            self.exchange.set_leverage(self.leverage, symbol)
            
            # Market order ile pozisyon aç
            order = self.exchange.create_market_order(
                symbol=symbol,
                side='buy',
                amount=position_size,
                params={'timeInForce': 'IOC'}  # Immediate or Cancel
            )
            
            # Stop Loss ve Take Profit orders
            sl_order = self.exchange.create_order(
                symbol=symbol,
                type='stop',
                side='sell',
                amount=position_size,
                price=None,
                params={'stopPrice': stop_loss, 'triggerBy': 'LastPrice'}
            )
            
            tp_order = self.exchange.create_order(
                symbol=symbol,
                type='limit',
                side='sell',
                amount=position_size,
                price=take_profit
            )
            
            # Position'ı kaydet
            self.open_positions[symbol] = {
                'entry_price': order['average'] or current_price,
                'quantity': position_size,
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'sl_order_id': sl_order['id'],
                'tp_order_id': tp_order['id'],
                'entry_time': datetime.now(),
                'rsi_entry': conditions['rsi'],
                'volume_ratio': conditions['volume_ratio'],
                'trailing_active': False,
                'highest_price': order['average'] or current_price
            }
            
            self.logger.info(f"🚀 {symbol} LONG pozisyon açıldı: {position_size:.6f} @ {order['average']:.2f}")
            self.logger.info(f"📍 SL: {stop_loss:.2f} | TP: {take_profit:.2f}")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Position open error for {symbol}: {e}")
            return False
            
    def check_exit_conditions(self, symbol: str) -> Tuple[bool, str]:
        """Exit koşullarını kontrol et"""
        try:
            if symbol not in self.open_positions:
                return False, ""
                
            # Current RSI check
            df = self.fetch_ohlcv_data(symbol, '1h', 50)
            if df.empty:
                return False, ""
                
            rsi_series = self.calculate_rsi(df['close'])
            current_rsi = rsi_series.iloc[-1]
            current_price = df['close'].iloc[-1]
            
            # RSI exit condition
            if current_rsi > self.rsi_overbought:
                return True, "RSI_EXIT"
                
            # Trailing stop management
            position = self.open_positions[symbol]
            entry_price = position['entry_price']
            profit_percent = (current_price - entry_price) / entry_price
            
            # Trailing stop logic
            if profit_percent >= self.trailing_trigger and not position['trailing_active']:
                # Trailing stop'u aktifleştir
                new_stop = entry_price * (1 + self.trailing_distance)
                self.update_stop_loss(symbol, new_stop)
                position['trailing_active'] = True
                self.logger.info(f"🔄 {symbol} Trailing stop activated at {new_stop:.2f}")
                
            # Update highest price for trailing
            if current_price > position['highest_price']:
                position['highest_price'] = current_price
                
                # Update trailing stop if active
                if position['trailing_active']:
                    new_stop = current_price * (1 - self.trailing_distance)
                    if new_stop > position['stop_loss']:
                        self.update_stop_loss(symbol, new_stop)
                        position['stop_loss'] = new_stop
                        
            return False, ""
            
        except Exception as e:
            self.logger.error(f"Exit condition check error for {symbol}: {e}")
            return False, ""
            
    def update_stop_loss(self, symbol: str, new_stop_price: float):
        """Stop loss güncelle"""
        try:
            position = self.open_positions[symbol]
            
            # Eski stop loss order'ı iptal et
            self.exchange.cancel_order(position['sl_order_id'], symbol)
            
            # Yeni stop loss order oluştur
            sl_order = self.exchange.create_order(
                symbol=symbol,
                type='stop',
                side='sell',
                amount=position['quantity'],
                price=None,
                params={'stopPrice': new_stop_price, 'triggerBy': 'LastPrice'}
            )
            
            # Order ID'yi güncelle
            position['sl_order_id'] = sl_order['id']
            position['stop_loss'] = new_stop_price
            
        except Exception as e:
            self.logger.error(f"Stop loss update error for {symbol}: {e}")
            
    def close_position(self, symbol: str, reason: str):
        """Pozisyonu kapat"""
        try:
            if symbol not in self.open_positions:
                return False
                
            position = self.open_positions[symbol]
            
            # Pending orders'ı iptal et
            try:
                self.exchange.cancel_order(position['sl_order_id'], symbol)
            except:
                pass
                
            try:
                self.exchange.cancel_order(position['tp_order_id'], symbol)
            except:
                pass
            
            # Market order ile kapat
            close_order = self.exchange.create_market_order(
                symbol=symbol,
                side='sell',
                amount=position['quantity']
            )
            
            # PnL hesapla
            exit_price = close_order['average']
            entry_price = position['entry_price']
            quantity = position['quantity']
            
            pnl_usdt = (exit_price - entry_price) * quantity
            pnl_percent = (exit_price - entry_price) / entry_price
            
            # Commission hesapla
            commission = (entry_price + exit_price) * quantity * 0.0004  # %0.04 taker fee
            net_pnl = pnl_usdt - commission
            
            # Trade'i logla
            trade_data = {
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'symbol': symbol,
                'side': 'LONG',
                'entry_price': entry_price,
                'exit_price': exit_price,
                'quantity': quantity,
                'pnl_usdt': round(net_pnl, 2),
                'pnl_percent': round(pnl_percent * 100, 2),
                'reason': reason,
                'rsi_entry': position['rsi_entry'],
                'volume_ratio': position['volume_ratio'],
                'commission_usdt': round(commission, 2)
            }
            
            self.log_trade(trade_data)
            
            # Consecutive loss tracking
            if net_pnl < 0:
                self.consecutive_losses += 1
                self.logger.warning(f"📉 Loss #{self.consecutive_losses}: {symbol} PnL: ${net_pnl:.2f}")
            else:
                self.consecutive_losses = 0
                self.logger.info(f"📈 Profit: {symbol} PnL: ${net_pnl:.2f}")
                
            # Check pause condition
            if self.consecutive_losses >= self.max_consecutive_losses:
                self.paused_until = datetime.now() + timedelta(hours=self.pause_hours)
                self.logger.warning(f"⏸️ PAUSED DUE TO 3 CONSECUTIVE LOSSES until {self.paused_until}")
                
            # Position'ı sil
            del self.open_positions[symbol]
            
            self.logger.info(f"❌ {symbol} position closed: {reason} | PnL: ${net_pnl:.2f} ({pnl_percent*100:.1f}%)")
            return True
            
        except Exception as e:
            self.logger.error(f"Position close error for {symbol}: {e}")
            return False
            
    def check_funding_rate(self, symbol: str):
        """Funding rate kontrolü"""
        try:
            if symbol not in self.open_positions:
                return
                
            funding_info = self.exchange.fetch_funding_rate(symbol)
            funding_rate = funding_info['fundingRate']
            
            # Yüksek pozitif funding rate kontrolü
            if funding_rate > 0.01:  # %1+ funding
                import random
                if random.random() < 0.7:  # %70 ihtimalle kapat
                    self.close_position(symbol, "HIGH_FUNDING_COST")
                    self.logger.info(f"💰 {symbol} closed due to high funding rate: {funding_rate:.4f}")
                    
        except Exception as e:
            self.logger.error(f"Funding rate check error for {symbol}: {e}")
            
    def manage_open_positions(self):
        """Açık pozisyonları yönet"""
        try:
            for symbol in list(self.open_positions.keys()):
                # Exit conditions check
                should_exit, reason = self.check_exit_conditions(symbol)
                if should_exit:
                    self.close_position(symbol, reason)
                    
                # Funding rate check (her 8 saatte bir)
                if datetime.now().hour % 8 == 0:
                    self.check_funding_rate(symbol)
                    
        except Exception as e:
            self.logger.error(f"Position management error: {e}")
            
    def is_paused(self) -> bool:
        """Bot pause durumunu kontrol et"""
        if self.paused_until and datetime.now() < self.paused_until:
            return True
        elif self.paused_until and datetime.now() >= self.paused_until:
            self.paused_until = None
            self.consecutive_losses = 0
            self.logger.info("▶️ Bot resumed after pause period")
            return False
        return False
        
    def analyze_and_trade(self, symbol: str):
        """Ana trading logic"""
        try:
            # Pause kontrolü
            if self.is_paused():
                return
                
            # Açık pozisyon varsa trade yapma
            if symbol in self.open_positions:
                return
                
            # Entry conditions check
            should_enter, conditions = self.check_entry_conditions(symbol)
            
            if should_enter:
                success = self.open_position(symbol, conditions)
                if success:
                    self.logger.info(f"✅ {symbol} position opened successfully")
                else:
                    self.logger.error(f"❌ Failed to open {symbol} position")
                    
        except Exception as e:
            self.logger.error(f"Trading analysis error for {symbol}: {e}")
            
    def run_bot(self):
        """Bot'u çalıştır"""
        self.logger.info("🤖 Crypto Trading Bot started")
        self.logger.info(f"📊 Symbols: {', '.join(self.symbols)}")
        self.logger.info(f"⚡ Leverage: {self.leverage}x")
        self.logger.info(f"🎯 Strategy: RSI<{self.rsi_oversold} + Volume>{self.volume_multiplier}x")
        
        while True:
            try:
                current_time = datetime.now()
                
                # Her saat başında (5. dakikada) analiz yap
                if current_time.minute == 5:
                    for symbol in self.symbols:
                        self.analyze_and_trade(symbol)
                        time.sleep(5)  # Rate limit koruması
                        
                # Her 10 dakikada açık pozisyonları kontrol et
                elif current_time.minute % 10 == 0:
                    self.manage_open_positions()
                    
                # Status log (her saat başında)
                elif current_time.minute == 0:
                    balance = self.get_account_balance()
                    open_pos_count = len(self.open_positions)
                    status = "PAUSED" if self.is_paused() else "ACTIVE"
                    self.logger.info(f"📊 Status: {status} | Balance: ${balance:.2f} | Open Positions: {open_pos_count}")
                    
                time.sleep(60)  # 1 dakika bekle
                
            except KeyboardInterrupt:
                self.logger.info("🛑 Bot stopped by user")
                break
            except Exception as e:
                self.logger.error(f"Main loop error: {e}")
                time.sleep(300)  # 5 dakika bekle
                
    def emergency_close_all(self):
        """Tüm pozisyonları acil kapat"""
        self.logger.warning("🚨 EMERGENCY CLOSE ALL POSITIONS")
        
        for symbol in list(self.open_positions.keys()):
            try:
                self.close_position(symbol, "EMERGENCY_CLOSE")
            except Exception as e:
                self.logger.error(f"Emergency close failed for {symbol}: {e}")
                
        self.logger.info("🚨 Emergency close completed")


def main():
    """Ana fonksiyon"""
    try:
        bot = CryptoTradingBot()
        bot.run_bot()
    except Exception as e:
        print(f"Bot initialization failed: {e}")
        

if __name__ == "__main__":
    main()