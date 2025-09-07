#!/usr/bin/env python3
"""
Acil Pozisyon Kapatma Script
Tüm açık pozisyonları anında kapatır
"""

import ccxt
import os
import sys
import logging
from datetime import datetime
from dotenv import load_dotenv
import json

load_dotenv()

class EmergencyCloser:
    def __init__(self):
        self.api_key = os.getenv('BYBIT_API_KEY')
        self.api_secret = os.getenv('BYBIT_API_SECRET')
        self.testnet = os.getenv('TESTNET', 'true').lower() == 'true'
        
        self.symbols = ['BTC/USDT:USDT', 'ETH/USDT:USDT']
        
        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s [%(levelname)s] %(message)s',
            handlers=[
                logging.FileHandler('logs/emergency_close.log'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
        
        # Setup exchange
        self.setup_exchange()
        
    def setup_exchange(self):
        """Exchange bağlantısını kur"""
        try:
            self.exchange = ccxt.bybit({
                'apiKey': self.api_key,
                'secret': self.api_secret,
                'sandbox': self.testnet,
                'enableRateLimit': True,
                'options': {
                    'defaultType': 'linear',
                    'recvWindow': 10000,
                }
            })
            
            # Test connection
            balance = self.exchange.fetch_balance()
            self.logger.info(f"✅ Exchange connected - Balance: {balance['USDT']['free']:.2f} USDT")
            
        except Exception as e:
            self.logger.error(f"❌ Exchange connection failed: {e}")
            sys.exit(1)
            
    def get_open_positions(self):
        """Açık pozisyonları al"""
        try:
            positions = self.exchange.fetch_positions()
            open_positions = []
            
            for position in positions:
                if position['contracts'] > 0:  # Açık pozisyon varsa
                    open_positions.append(position)
                    
            return open_positions
            
        except Exception as e:
            self.logger.error(f"❌ Error fetching positions: {e}")
            return []
            
    def cancel_all_orders(self, symbol):
        """Tüm pending orders'ı iptal et"""
        try:
            orders = self.exchange.fetch_open_orders(symbol)
            cancelled_count = 0
            
            for order in orders:
                try:
                    self.exchange.cancel_order(order['id'], symbol)
                    cancelled_count += 1
                    self.logger.info(f"❌ Cancelled order: {order['id']} ({order['type']} {order['side']})")
                except Exception as e:
                    self.logger.warning(f"⚠️ Failed to cancel order {order['id']}: {e}")
                    
            if cancelled_count > 0:
                self.logger.info(f"📋 Cancelled {cancelled_count} orders for {symbol}")
            else:
                self.logger.info(f"📋 No pending orders for {symbol}")
                
        except Exception as e:
            self.logger.error(f"❌ Error cancelling orders for {symbol}: {e}")
            
    def close_position(self, position):
        """Pozisyonu kapat"""
        try:
            symbol = position['symbol']
            size = abs(position['contracts'])
            side = 'sell' if position['side'] == 'long' else 'buy'
            
            self.logger.info(f"🔄 Closing {position['side'].upper()} position: {size} {symbol}")
            
            # Market order ile kapat
            order = self.exchange.create_market_order(
                symbol=symbol,
                side=side,
                amount=size,
                params={'timeInForce': 'IOC', 'reduceOnly': True}
            )
            
            # PnL hesapla
            entry_price = position['entryPrice']
            exit_price = order['average'] or position['markPrice']
            
            if position['side'] == 'long':
                pnl_percent = (exit_price - entry_price) / entry_price
            else:
                pnl_percent = (entry_price - exit_price) / entry_price
                
            pnl_usdt = pnl_percent * size * entry_price
            
            self.logger.info(f"✅ Position closed: {symbol}")
            self.logger.info(f"💰 PnL: ${pnl_usdt:.2f} ({pnl_percent*100:.2f}%)")
            
            return {
                'symbol': symbol,
                'side': position['side'],
                'size': size,
                'entry_price': entry_price,
                'exit_price': exit_price,
                'pnl_usdt': pnl_usdt,
                'pnl_percent': pnl_percent * 100,
                'timestamp': datetime.now()
            }
            
        except Exception as e:
            self.logger.error(f"❌ Failed to close position {position['symbol']}: {e}")
            return None
            
    def emergency_close_all(self):
        """Tüm pozisyonları acil kapat"""
        self.logger.warning("🚨 EMERGENCY CLOSE ALL POSITIONS INITIATED")
        self.logger.warning("⚠️ This will close ALL open positions immediately!")
        
        # User confirmation
        if not self.testnet:
            confirm = input("❓ Are you sure you want to close ALL positions? (type 'YES' to confirm): ")
            if confirm != 'YES':
                self.logger.info("❌ Emergency close cancelled by user")
                return
                
        # Get open positions
        positions = self.get_open_positions()
        
        if not positions:
            self.logger.info("✅ No open positions found")
            return
            
        self.logger.info(f"📊 Found {len(positions)} open positions")
        
        closed_positions = []
        
        # Cancel all pending orders first
        for symbol in self.symbols:
            self.cancel_all_orders(symbol)
            
        # Close all positions
        for position in positions:
            closed_position = self.close_position(position)
            if closed_position:
                closed_positions.append(closed_position)
                
        # Summary
        if closed_positions:
            total_pnl = sum([pos['pnl_usdt'] for pos in closed_positions])
            self.logger.info(f"📊 EMERGENCY CLOSE SUMMARY:")
            self.logger.info(f"   Positions Closed: {len(closed_positions)}")
            self.logger.info(f"   Total PnL: ${total_pnl:.2f}")
            
            # Save emergency close log
            self.save_emergency_log(closed_positions, total_pnl)
        else:
            self.logger.warning("❌ No positions were successfully closed")
            
        self.logger.warning("🚨 EMERGENCY CLOSE COMPLETED")
        
    def save_emergency_log(self, closed_positions, total_pnl):
        """Acil kapatma logunu kaydet"""
        try:
            os.makedirs('logs', exist_ok=True)
            
            emergency_data = {
                'timestamp': datetime.now().isoformat(),
                'total_positions_closed': len(closed_positions),
                'total_pnl': total_pnl,
                'positions': []
            }
            
            for pos in closed_positions:
                emergency_data['positions'].append({
                    'symbol': pos['symbol'],
                    'side': pos['side'],
                    'size': pos['size'],
                    'entry_price': pos['entry_price'],
                    'exit_price': pos['exit_price'],
                    'pnl_usdt': pos['pnl_usdt'],
                    'pnl_percent': pos['pnl_percent'],
                    'timestamp': pos['timestamp'].isoformat()
                })
                
            # Save to JSON
            filename = f"logs/emergency_close_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(filename, 'w') as f:
                json.dump(emergency_data, f, indent=2)
                
            self.logger.info(f"💾 Emergency close log saved: {filename}")
            
        except Exception as e:
            self.logger.error(f"❌ Failed to save emergency log: {e}")
            
    def show_positions_status(self):
        """Mevcut pozisyon durumunu göster"""
        self.logger.info("📊 Checking current positions...")
        
        positions = self.get_open_positions()
        
        if not positions:
            self.logger.info("✅ No open positions")
            return
            
        self.logger.info(f"📈 Open Positions ({len(positions)}):")
        total_unrealized = 0
        
        for pos in positions:
            unrealized_pnl = pos['unrealizedPnl'] or 0
            total_unrealized += unrealized_pnl
            
            self.logger.info(f"   {pos['symbol']}: {pos['side'].upper()} {pos['contracts']} "
                           f"@ {pos['entryPrice']:.2f} | PnL: ${unrealized_pnl:.2f}")
            
        self.logger.info(f"💰 Total Unrealized PnL: ${total_unrealized:.2f}")
        
    def show_balance(self):
        """Mevcut bakiyeyi göster"""
        try:
            balance = self.exchange.fetch_balance()
            usdt_balance = balance['USDT']
            
            self.logger.info(f"💰 USDT Balance:")
            self.logger.info(f"   Free: ${usdt_balance['free']:.2f}")
            self.logger.info(f"   Used: ${usdt_balance['used']:.2f}")
            self.logger.info(f"   Total: ${usdt_balance['total']:.2f}")
            
        except Exception as e:
            self.logger.error(f"❌ Failed to fetch balance: {e}")


def main():
    """Ana fonksiyon"""
    print("🚨 EMERGENCY POSITION CLOSER")
    print("="*40)
    
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python emergency_close.py status    - Show current positions")
        print("  python emergency_close.py balance   - Show account balance")
        print("  python emergency_close.py close     - Close ALL positions")
        print("  python emergency_close.py force     - Force close without confirmation")
        sys.exit(1)
        
    command = sys.argv[1].lower()
    
    try:
        closer = EmergencyCloser()
        
        if command == 'status':
            closer.show_positions_status()
            
        elif command == 'balance':
            closer.show_balance()
            
        elif command == 'close':
            closer.emergency_close_all()
            
        elif command == 'force':
            # Force mode - skip confirmation even in mainnet
            original_testnet = closer.testnet
            closer.testnet = True  # Temporarily set to avoid confirmation
            closer.emergency_close_all()
            closer.testnet = original_testnet
            
        else:
            print(f"❌ Unknown command: {command}")
            sys.exit(1)
            
    except KeyboardInterrupt:
        print("\n🛑 Emergency close interrupted by user")
    except Exception as e:
        print(f"❌ Emergency close failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()