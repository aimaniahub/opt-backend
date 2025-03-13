import csv
from typing import List, Dict, Optional
import requests
import json
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
import re

def parse_number(value: str, num_type=float):
    """Convert CSV string (with commas or '-') to int/float."""
    if value.strip() in ('-', ''):
        return 0
    try:
        cleaned = value.replace(',', '')
        return num_type(cleaned)
    except:
        return 0

def read_option_chain(file_path: str) -> List[Dict]:
    """Reads the option chain CSV and returns structured data."""
    options = []
    with open(file_path, 'r') as f:
        reader = csv.reader(f)
        next(reader)  # Skip header row
        next(reader)  # Skip subheader row
        
        for row in reader:
            if len(row) < 21:  # Basic validation
                continue
                
            # CALLS (left side)
            call_oi = parse_number(row[1], int)
            call_oi_chng = parse_number(row[2], int)
            call_volume = parse_number(row[3], int)
            call_iv = parse_number(row[4])
            call_ltp = parse_number(row[5])
            call_chng = parse_number(row[6])
            call_bid = parse_number(row[8])
            call_ask = parse_number(row[9])
            
            # Strike Price (middle)
            strike = parse_number(row[11])
            
            # PUTS (right side)
            put_bid = parse_number(row[13])
            put_ask = parse_number(row[14])
            put_chng = parse_number(row[16])
            put_ltp = parse_number(row[17])
            put_iv = parse_number(row[18])
            put_volume = parse_number(row[19])
            put_oi_chng = parse_number(row[20], int)
            put_oi = parse_number(row[21], int)
            
            options.append({
                'strike': strike,
                'call_oi': call_oi,
                'call_oi_chng': call_oi_chng,
                'call_volume': call_volume,
                'call_iv': call_iv,
                'call_ltp': call_ltp,
                'call_chng': call_chng,
                'call_bid': call_bid,
                'call_ask': call_ask,
                'put_oi': put_oi,
                'put_oi_chng': put_oi_chng,
                'put_volume': put_volume,
                'put_iv': put_iv,
                'put_ltp': put_ltp,
                'put_chng': put_chng,
                'put_bid': put_bid,
                'put_ask': put_ask,
            })
    return options

def find_max_put_oi_strike(options: List[Dict]) -> float:
    """Identify the strike with the highest PUT OI."""
    max_oi = -1
    key_strike = 0
    for opt in options:
        if opt['put_oi'] > max_oi:
            max_oi = opt['put_oi']
            key_strike = opt['strike']
    return key_strike

def analyze_calls(options: List[Dict], current_price: float) -> List[Dict]:
    """Filter CALL options with high OI change, volume and tight spreads."""
    candidates = []
    for opt in options:
        # Only consider strikes near the current price (within ±5%)
        if abs(opt['strike'] - current_price) / current_price <= 0.05:
            if (opt['call_oi_chng'] > 0 and 
                opt['call_volume'] > 1000 and 
                opt['call_ltp'] > 0):
                
                spread = opt['call_ask'] - opt['call_bid']
                spread_percentage = spread / opt['call_ltp'] * 100
                
                if spread_percentage < 5:
                    candidates.append({
                        'strike': opt['strike'],
                        'buy_price': opt['call_ask'],
                        'exit': opt['call_ask'] * 1.5,
                        'stop_loss': opt['call_bid'] * 0.7,
                        'oi_chng': opt['call_oi_chng'],
                        'volume': opt['call_volume'],
                        'iv': opt['call_iv'],
                        'reason': f"OI Change: {opt['call_oi_chng']}, Volume: {opt['call_volume']}"
                    })
    
    candidates.sort(key=lambda x: (x['oi_chng'], x['volume']), reverse=True)
    return candidates

def analyze_puts(options: List[Dict], current_price: float) -> List[Dict]:
    """Filter PUT options with high OI change and volume."""
    candidates = []
    for opt in options:
        # Only consider strikes near the current price (within ±5%)
        if abs(opt['strike'] - current_price) / current_price <= 0.05:
            if (opt['put_oi_chng'] > 0 and 
                opt['put_volume'] > 1000 and
                opt['put_ltp'] > 0):
                
                spread = opt['put_ask'] - opt['put_bid']
                spread_percentage = spread / opt['put_ltp'] * 100
                
                if spread_percentage < 5:
                    candidates.append({
                        'strike': opt['strike'],
                        'buy_price': opt['put_ask'],
                        'exit': opt['put_ask'] * 1.5,
                        'stop_loss': opt['put_bid'] * 0.7,
                        'oi_chng': opt['put_oi_chng'],
                        'volume': opt['put_volume'],
                        'iv': opt['put_iv'],
                        'reason': f"OI Change: {opt['put_oi_chng']}, Volume: {opt['put_volume']}"
                    })
    
    candidates.sort(key=lambda x: (x['oi_chng'], x['volume']), reverse=True)
    return candidates

def analyze_otm_calls(options: List[Dict], current_price: float) -> List[Dict]:
    """Identify OTM CALLs based on current price."""
    candidates = []
    for opt in options:
        # Consider strikes 2-10% above current price for OTM calls
        if 1.02 * current_price <= opt['strike'] <= 1.10 * current_price:
            if opt['call_oi_chng'] > 0:
                spread = opt['call_ask'] - opt['call_bid']
                if spread < 2.0:
                    candidates.append({
                        'strike': opt['strike'],
                        'buy_price': opt['call_ask'],
                        'exit': opt['call_ask'] * 2,
                        'stop_loss': opt['call_bid'] * 0.7,
                        'oi_chng': opt['call_oi_chng'],
                        'volume': opt['call_volume'],
                        'iv': opt['call_iv'],
                        'reason': f"OTM with OI buildup, Volume: {opt['call_volume']}"
                    })
    candidates.sort(key=lambda x: x['oi_chng'], reverse=True)
    return candidates

def analyze_market_direction(options: List[Dict], current_price: float) -> Dict:
    """Analyze market direction based on option chain data."""
    call_oi_sum = 0
    put_oi_sum = 0
    call_volume_sum = 0
    put_volume_sum = 0
    max_call_oi = {'strike': 0, 'oi': 0}
    max_put_oi = {'strike': 0, 'oi': 0}
    
    for opt in options:
        # Consider only strikes within ±5% of current price
        if abs(opt['strike'] - current_price) / current_price <= 0.05:
            call_oi_sum += opt['call_oi']
            put_oi_sum += opt['put_oi']
            call_volume_sum += opt['call_volume']
            put_volume_sum += opt['put_volume']
            
            if opt['call_oi'] > max_call_oi['oi']:
                max_call_oi = {'strike': opt['strike'], 'oi': opt['call_oi']}
            if opt['put_oi'] > max_put_oi['oi']:
                max_put_oi = {'strike': opt['strike'], 'oi': opt['put_oi']}

    pcr = put_oi_sum / call_oi_sum if call_oi_sum > 0 else 0
    volume_ratio = put_volume_sum / call_volume_sum if call_volume_sum > 0 else 0
    
    # Determine market bias
    bias = "Neutral"
    target_price = current_price
    confidence = 0
    reason = []
    
    if pcr > 1.5:
        bias = "Bullish"
        confidence += 30
        reason.append("High Put-Call Ratio indicates potential reversal")
    elif pcr < 0.7:
        bias = "Bearish"
        confidence += 30
        reason.append("Low Put-Call Ratio indicates potential reversal")
        
    if volume_ratio > 1.2:
        if bias == "Bearish":
            confidence += 20
        reason.append("Higher Put volume indicates bearish sentiment")
    elif volume_ratio < 0.8:
        if bias == "Bullish":
            confidence += 20
        reason.append("Higher Call volume indicates bullish sentiment")
    
    # Target price calculation
    if bias == "Bullish":
        target_price = max(max_call_oi['strike'], current_price * 1.01)
    elif bias == "Bearish":
        target_price = min(max_put_oi['strike'], current_price * 0.99)
        
    return {
        'bias': bias,
        'confidence': min(confidence, 100),
        'target_price': target_price,
        'pcr': pcr,
        'reason': '. '.join(reason),
        'max_call_oi_strike': max_call_oi['strike'],
        'max_put_oi_strike': max_put_oi['strike']
    }

def print_results(key_strike: float, calls: List[Dict], puts: List[Dict], otm_calls: List[Dict]):
    """Display results in a structured format."""
    print(f"\n{'='*60}")
    print(f"Key Strike (Highest PUT OI): {key_strike:,.2f}")
    print(f"{'='*60}\n")

    # Helper function to safely print values
    def safe_print(data: List[Dict], title: str):
        print(f"{title}:")
        print("-"*60)
        for item in data[:3]:
            try:
                print(f"Strike: {item['strike']:,.2f}")
                print(f"  Buy Price: ₹{item['buy_price']:.2f}")
                print(f"  Exit Target: ₹{item['exit']:.2f}")
                print(f"  Stop Loss: ₹{item['stop_loss']:.2f}")
                print(f"  OI Change: {item['oi_chng']:,d}")
                print(f"  Volume: {int(item.get('volume', 0)):,d}")
                print(f"  IV: {item.get('iv', 0):.2f}%")
                print(f"  Reason: {item['reason']}\n")
            except Exception as e:
                print(f"Error printing data: {e}\n")
                continue

    safe_print(calls, "CALL Buying Opportunities")
    safe_print(puts, "\nPUT Trading Opportunities")
    safe_print(otm_calls, "\nOTM CALL Opportunities")

def analyze_best_trades(options: List[Dict], current_price: float, volume_signals: Dict = None) -> Dict:
    """Analyze and select the best trading opportunities with volume data."""
    atm_range = 0.02  # 2% range for ATM
    otm_range = (0.02, 0.10)  # 2-10% range for OTM
    
    # Initialize containers for opportunities
    atm_opportunities = []
    otm_opportunities = []
    
    # Volume bias factor (default to neutral if no volume data)
    volume_bias = 0
    if volume_signals:
        volume_bias = volume_signals.get('volume_score', 0)
    
    for opt in options:
        price_diff_pct = (opt['strike'] - current_price) / current_price
        
        # ATM Analysis (within ±2% of current price)
        if abs(price_diff_pct) <= atm_range:
            # Analyze CALL side
            if opt['call_oi_chng'] > 0 and opt['call_volume'] > 1000:
                call_spread = opt['call_ask'] - opt['call_bid']
                call_spread_pct = call_spread / opt['call_ltp'] if opt['call_ltp'] > 0 else float('inf')
                
                if call_spread_pct < 0.05:  # 5% spread threshold
                    # Add market bias factor to score
                    market_bias_factor = 1.2 if price_diff_pct < 0 else 0.9  # Favor ITM calls
                    
                    # Calculate volume to OI ratio (higher is better)
                    vol_oi_ratio = opt['call_volume'] / opt['call_oi'] if opt['call_oi'] > 0 else 0
                    
                    # Add volume bias to score (positive volume bias boosts calls)
                    volume_factor = 1 + (volume_bias / 20) if volume_bias > 0 else 1
                    
                    # Enhanced score calculation with volume factor
                    enhanced_score = calculate_score(
                        opt['call_oi_chng'], 
                        opt['call_volume'], 
                        call_spread_pct, 
                        opt['call_iv']
                    ) * market_bias_factor * (1 + min(vol_oi_ratio * 0.1, 0.5)) * volume_factor
                    
                    # Create reason text including volume data
                    volume_reason = ""
                    if volume_signals and volume_signals.get('volume_signal'):
                        volume_reason = f" Volume signal: {volume_signals['volume_signal']}"
                    
                    atm_opportunities.append({
                        'type': 'CALL',
                        'strike': opt['strike'],
                        'buy_price': opt['call_ask'],
                        'exit': opt['call_ask'] * 1.5,
                        'stop_loss': opt['call_bid'] * 0.7,
                        'oi_chng': opt['call_oi_chng'],
                        'volume': opt['call_volume'],
                        'iv': opt['call_iv'],
                        'score': enhanced_score,
                        'reason': f"ATM CALL with strong OI buildup and volume.{volume_reason}"
                    })
            
            # Analyze PUT side with similar enhancements
            if opt['put_oi_chng'] > 0 and opt['put_volume'] > 1000:
                put_spread = opt['put_ask'] - opt['put_bid']
                put_spread_pct = put_spread / opt['put_ltp'] if opt['put_ltp'] > 0 else float('inf')
                
                if put_spread_pct < 0.05:
                    # Add market bias factor
                    market_bias_factor = 1.2 if price_diff_pct > 0 else 0.9  # Favor ITM puts
                    
                    # Calculate volume to OI ratio
                    vol_oi_ratio = opt['put_volume'] / opt['put_oi'] if opt['put_oi'] > 0 else 0
                    
                    # Add volume bias to score (negative volume bias boosts puts)
                    volume_factor = 1 + (abs(volume_bias) / 20) if volume_bias < 0 else 1
                    
                    # Enhanced score calculation with volume factor
                    enhanced_score = calculate_score(
                        opt['put_oi_chng'], 
                        opt['put_volume'], 
                        put_spread_pct, 
                        opt['put_iv']
                    ) * market_bias_factor * (1 + min(vol_oi_ratio * 0.1, 0.5)) * volume_factor
                    
                    # Create reason text including volume data
                    volume_reason = ""
                    if volume_signals and volume_signals.get('volume_signal'):
                        volume_reason = f" Volume signal: {volume_signals['volume_signal']}"
                    
                    atm_opportunities.append({
                        'type': 'PUT',
                        'strike': opt['strike'],
                        'buy_price': opt['put_ask'],
                        'exit': opt['put_ask'] * 1.5,
                        'stop_loss': opt['put_bid'] * 0.7,
                        'oi_chng': opt['put_oi_chng'],
                        'volume': opt['put_volume'],
                        'iv': opt['put_iv'],
                        'score': enhanced_score,
                        'reason': f"ATM PUT with strong OI buildup and volume.{volume_reason}"
                    })
        
        # OTM Analysis with enhanced scoring
        elif otm_range[0] < abs(price_diff_pct) <= otm_range[1]:
            if price_diff_pct > 0:  # OTM CALL
                if opt['call_oi_chng'] > 0 and opt['call_volume'] > 500:
                    spread = opt['call_ask'] - opt['call_bid']
                    spread_pct = spread / opt['call_ltp'] if opt['call_ltp'] > 0 else float('inf')
                    
                    if spread_pct < 0.08:
                        # Calculate risk-reward ratio
                        risk = opt['call_ask'] - opt['call_bid'] * 0.6
                        reward = opt['call_ask'] * 2 - opt['call_ask']
                        risk_reward = reward / risk if risk > 0 else 0
                        
                        # Add volume bias to score (positive volume bias boosts calls)
                        volume_factor = 1 + (volume_bias / 20) if volume_bias > 0 else 1
                        
                        # Enhanced score with risk-reward factor and volume
                        enhanced_score = calculate_score(
                            opt['call_oi_chng'], 
                            opt['call_volume'], 
                            spread_pct, 
                            opt['call_iv']
                        ) * min(risk_reward * 0.2, 1.5) * volume_factor
                        
                        # Create reason text including volume data
                        volume_reason = ""
                        if volume_signals and volume_signals.get('volume_signal'):
                            volume_reason = f" Volume signal: {volume_signals['volume_signal']}"
                        
                        otm_opportunities.append({
                            'type': 'CALL',
                            'strike': opt['strike'],
                            'buy_price': opt['call_ask'],
                            'exit': opt['call_ask'] * 2,
                            'stop_loss': opt['call_bid'] * 0.6,
                            'oi_chng': opt['call_oi_chng'],
                            'volume': opt['call_volume'],
                            'iv': opt['call_iv'],
                            'score': enhanced_score,
                            'reason': f"OTM CALL with potential momentum, Risk:Reward = 1:{risk_reward:.1f}.{volume_reason}"
                        })
            else:  # OTM PUT with enhanced scoring
                if opt['put_oi_chng'] > 0 and opt['put_volume'] > 500:
                    spread = opt['put_ask'] - opt['put_bid']
                    spread_pct = spread / opt['put_ltp'] if opt['put_ltp'] > 0 else float('inf')
                    
                    if spread_pct < 0.08:
                        # Calculate risk-reward ratio
                        risk = opt['put_ask'] - opt['put_bid'] * 0.6
                        reward = opt['put_ask'] * 2 - opt['put_ask']
                        risk_reward = reward / risk if risk > 0 else 0
                        
                        # Add volume bias to score (negative volume bias boosts puts)
                        volume_factor = 1 + (abs(volume_bias) / 20) if volume_bias < 0 else 1
                        
                        # Enhanced score with risk-reward factor and volume
                        enhanced_score = calculate_score(
                            opt['put_oi_chng'], 
                            opt['put_volume'], 
                            spread_pct, 
                            opt['put_iv']
                        ) * min(risk_reward * 0.2, 1.5) * volume_factor
                        
                        # Create reason text including volume data
                        volume_reason = ""
                        if volume_signals and volume_signals.get('volume_signal'):
                            volume_reason = f" Volume signal: {volume_signals['volume_signal']}"
                        
                        otm_opportunities.append({
                            'type': 'PUT',
                            'strike': opt['strike'],
                            'buy_price': opt['put_ask'],
                            'exit': opt['put_ask'] * 2,
                            'stop_loss': opt['put_bid'] * 0.6,
                            'oi_chng': opt['put_oi_chng'],
                            'volume': opt['put_volume'],
                            'iv': opt['put_iv'],
                            'score': enhanced_score,
                            'reason': f"OTM PUT with potential momentum, Risk:Reward = 1:{risk_reward:.1f}.{volume_reason}"
                        })
    
    # Sort opportunities by score
    atm_opportunities.sort(key=lambda x: x['score'], reverse=True)
    otm_opportunities.sort(key=lambda x: x['score'], reverse=True)
    
    # Get the absolute best trade
    all_opportunities = atm_opportunities + otm_opportunities
    all_opportunities.sort(key=lambda x: x['score'], reverse=True)
    
    return {
        'best_overall': all_opportunities[:1],  # The single best trade
        'best_atm': atm_opportunities[:2],      # Top 2 ATM opportunities
        'best_otm': otm_opportunities[:2]       # Top 2 OTM opportunities
    }

def calculate_score(oi_change: float, volume: float, spread_pct: float, iv: float) -> float:
    """Calculate a score for ranking trade opportunities."""
    oi_score = min(oi_change / 1000, 10)  # Cap at 10
    volume_score = min(volume / 5000, 10)  # Cap at 10
    spread_score = max(10 - (spread_pct * 100), 0)  # Lower spread is better
    iv_score = min(iv / 5, 10)  # Cap at 10
    
    # Weighted scoring
    return (oi_score * 0.4 +        # 40% weight to OI change
            volume_score * 0.3 +     # 30% weight to volume
            spread_score * 0.2 +     # 20% weight to spread
            iv_score * 0.1)         # 10% weight to IV

def fetch_volume_data(symbol: str) -> Dict:
    """Fetch volume inflow/outflow data for a given stock from NSE."""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        session = requests.Session()
        # Get cookies first
        session.get("https://www.nseindia.com", headers=headers)
        
        # For indices, we need to use a different approach
        if symbol in ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]:
            # For indices, we'll use the advances/declines data as a proxy for inflow/outflow
            url = "https://www.nseindia.com/api/equity-stockIndices?index=SECURITIES%20IN%20F%26O"
        else:
            # For individual stocks, fetch the stock quote data
            url = f"https://www.nseindia.com/api/quote-equity?symbol={symbol}"
        
        response = session.get(url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            
            if symbol in ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]:
                # For indices, calculate the inflow/outflow based on advances vs declines
                advances = data.get('advance', {}).get('advances', 0)
                declines = data.get('advance', {}).get('declines', 0)
                unchanged = data.get('advance', {}).get('unchanged', 0)
                
                total = advances + declines + unchanged
                inflow_ratio = advances / total if total > 0 else 0
                outflow_ratio = declines / total if total > 0 else 0
                
                return {
                    'symbol': symbol,
                    'inflow': advances,
                    'outflow': declines,
                    'unchanged': unchanged,
                    'inflow_ratio': inflow_ratio,
                    'outflow_ratio': outflow_ratio,
                    'net_flow': advances - declines,
                    'success': True
                }
            else:
                # For stocks, extract the trading data
                trading_data = data.get('marketDeptOrderBook', {}).get('tradeInfo', {})
                total_buy_qty = float(trading_data.get('totalBuyQuantity', 0))
                total_sell_qty = float(trading_data.get('totalSellQuantity', 0))
                
                # Calculate inflow/outflow ratios
                total_qty = total_buy_qty + total_sell_qty
                inflow_ratio = total_buy_qty / total_qty if total_qty > 0 else 0
                outflow_ratio = total_sell_qty / total_qty if total_qty > 0 else 0
                
                # Get delivery percentage as additional signal
                delivery_data = data.get('securityWiseDP', {})
                delivery_qty = float(delivery_data.get('deliveryQuantity', 0))
                traded_qty = float(delivery_data.get('tradedQuantity', 0))
                delivery_percentage = (delivery_qty / traded_qty * 100) if traded_qty > 0 else 0
                
                # Get price change data
                price_data = data.get('priceInfo', {})
                change = float(price_data.get('change', 0))
                pct_change = float(price_data.get('pChange', 0))
                
                return {
                    'symbol': symbol,
                    'total_buy_qty': total_buy_qty,
                    'total_sell_qty': total_sell_qty,
                    'inflow_ratio': inflow_ratio,
                    'outflow_ratio': outflow_ratio,
                    'net_flow': total_buy_qty - total_sell_qty,
                    'delivery_percentage': delivery_percentage,
                    'price_change': change,
                    'price_change_percent': pct_change,
                    'success': True
                }
        else:
            return {
                'symbol': symbol,
                'success': False,
                'error': f"Failed to fetch volume data: {response.status_code}"
            }
    except Exception as e:
        return {
            'symbol': symbol,
            'success': False,
            'error': str(e)
        }

def fetch_historical_volume(symbol: str, days: int = 5) -> Dict:
    """Fetch historical volume data for trend analysis."""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        session = requests.Session()
        # Get cookies first
        session.get("https://www.nseindia.com", headers=headers)
        
        # Calculate date range
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        
        # Format dates for API
        from_date = start_date.strftime('%d-%m-%Y')
        to_date = end_date.strftime('%d-%m-%Y')
        
        if symbol in ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]:
            # For indices
            index_map = {
                "NIFTY": "NIFTY 50",
                "BANKNIFTY": "NIFTY BANK",
                "FINNIFTY": "NIFTY FINANCIAL SERVICES",
                "MIDCPNIFTY": "NIFTY MIDCAP SELECT"
            }
            index_name = index_map.get(symbol, "NIFTY 50")
            url = f"https://www.nseindia.com/api/historical/indicesHistory?indexType={index_name}&from={from_date}&to={to_date}"
        else:
            # For stocks
            url = f"https://www.nseindia.com/api/historical/cm/equity?symbol={symbol}&from={from_date}&to={to_date}"
        
        response = session.get(url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            
            # Extract historical data
            history = data.get('data', [])
            
            # Process the data
            volume_trend = []
            for day in history:
                date = day.get('date', '')
                volume = float(day.get('volume', 0)) if 'volume' in day else float(day.get('VOLUME', 0))
                close = float(day.get('close', 0)) if 'close' in day else float(day.get('CLOSE', 0))
                
                volume_trend.append({
                    'date': date,
                    'volume': volume,
                    'close': close
                })
            
            # Calculate volume trend
            if len(volume_trend) > 1:
                avg_volume = sum(day['volume'] for day in volume_trend) / len(volume_trend)
                latest_volume = volume_trend[0]['volume'] if volume_trend else 0
                volume_change = (latest_volume / avg_volume - 1) * 100 if avg_volume > 0 else 0
            else:
                avg_volume = 0
                latest_volume = 0
                volume_change = 0
            
            return {
                'symbol': symbol,
                'volume_trend': volume_trend,
                'avg_volume': avg_volume,
                'latest_volume': latest_volume,
                'volume_change_percent': volume_change,
                'success': True
            }
        else:
            return {
                'symbol': symbol,
                'success': False,
                'error': f"Failed to fetch historical data: {response.status_code}"
            }
    except Exception as e:
        return {
            'symbol': symbol,
            'success': False,
            'error': str(e)
        }

def analyze_volume_signals(volume_data: Dict) -> Dict:
    """Analyze volume data to generate trading signals."""
    signals = {
        'volume_score': 0,
        'volume_signal': 'Neutral',
        'reasons': []
    }
    
    # Check if we have valid data
    if not volume_data.get('success'):
        return signals
    
    # Calculate volume score based on inflow/outflow
    if 'inflow_ratio' in volume_data and 'outflow_ratio' in volume_data:
        inflow = volume_data['inflow_ratio']
        outflow = volume_data['outflow_ratio']
        
        # Score from -10 to +10
        volume_score = (inflow - outflow) * 20  # Scale to -10 to +10
        
        if volume_score > 3:
            signals['volume_signal'] = 'Strong Bullish'
            signals['reasons'].append(f"Strong buying pressure with {inflow:.1%} inflow ratio")
        elif volume_score > 1:
            signals['volume_signal'] = 'Bullish'
            signals['reasons'].append(f"Moderate buying with {inflow:.1%} inflow ratio")
        elif volume_score < -3:
            signals['volume_signal'] = 'Strong Bearish'
            signals['reasons'].append(f"Strong selling pressure with {outflow:.1%} outflow ratio")
        elif volume_score < -1:
            signals['volume_signal'] = 'Bearish'
            signals['reasons'].append(f"Moderate selling with {outflow:.1%} outflow ratio")
        else:
            signals['volume_signal'] = 'Neutral'
            signals['reasons'].append("Balanced buying and selling pressure")
        
        signals['volume_score'] = volume_score
    
    # Add delivery percentage analysis if available
    if 'delivery_percentage' in volume_data:
        delivery_pct = volume_data['delivery_percentage']
        
        if delivery_pct > 60:
            delivery_score = 3
            signals['reasons'].append(f"High delivery percentage ({delivery_pct:.1f}%) indicates strong conviction")
        elif delivery_pct > 40:
            delivery_score = 1
            signals['reasons'].append(f"Good delivery percentage ({delivery_pct:.1f}%) shows investor interest")
        else:
            delivery_score = 0
            
        # Adjust volume score with delivery data
        signals['volume_score'] += delivery_score
    
    return signals

def fetch_stock_news(symbol: str) -> Dict:
    """Fetch latest news for a given stock symbol."""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        # Try multiple sources for news
        news_items = []
        
        # 1. Try NSE website news
        session = requests.Session()
        session.get("https://www.nseindia.com", headers=headers)
        
        # NSE company info API
        nse_url = f"https://www.nseindia.com/api/quote-equity?symbol={symbol}"
        response = session.get(nse_url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            if 'news' in data:
                for news in data.get('news', [])[:3]:  # Get latest 3 news items
                    news_items.append({
                        'title': news.get('title', ''),
                        'date': news.get('date', ''),
                        'source': 'NSE',
                        'url': news.get('url', '')
                    })
        
        # 2. Try MoneyControl (as backup)
        if len(news_items) < 3:
            mc_url = f"https://www.moneycontrol.com/stocks/company_info/stock_news.php?sc_id={symbol}"
            response = requests.get(mc_url, headers=headers)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                news_divs = soup.find_all('div', class_='item')
                
                for news in news_divs[:3]:
                    title = news.find('h3')
                    date = news.find('span', class_='date')
                    if title and date:
                        news_items.append({
                            'title': title.text.strip(),
                            'date': date.text.strip(),
                            'source': 'MoneyControl',
                            'url': news.find('a')['href'] if news.find('a') else ''
                        })
        
        # Analyze sentiment for each news item
        for item in news_items:
            sentiment = analyze_news_sentiment(item['title'])
            item['sentiment'] = sentiment['sentiment']
            item['sentiment_score'] = sentiment['score']
            item['impact_factors'] = sentiment['impact_factors']
        
        return {
            'success': True,
            'news': news_items,
            'overall_sentiment': calculate_overall_sentiment(news_items)
        }
        
    except Exception as e:
        print(f"Error fetching news for {symbol}: {str(e)}")
        return {
            'success': False,
            'error': str(e),
            'news': []
        }

def analyze_news_sentiment(text: str) -> Dict:
    """Analyze the sentiment of news text."""
    # Keywords for sentiment analysis
    bullish_keywords = [
        'surge', 'jump', 'rise', 'gain', 'up', 'higher', 'boost', 'growth', 'profit',
        'positive', 'strong', 'beat', 'exceed', 'upgrade', 'buy', 'bullish', 'record',
        'partnership', 'launch', 'expansion', 'innovation', 'contract', 'win', 'success'
    ]
    
    bearish_keywords = [
        'fall', 'drop', 'decline', 'down', 'lower', 'loss', 'weak', 'miss', 'below',
        'downgrade', 'sell', 'bearish', 'cut', 'reduce', 'risk', 'concern', 'debt',
        'investigation', 'lawsuit', 'penalty', 'fine', 'delay', 'recall', 'dispute'
    ]
    
    # Count occurrences
    bullish_count = sum(1 for word in bullish_keywords if word.lower() in text.lower())
    bearish_count = sum(1 for word in bearish_keywords if word.lower() in text.lower())
    
    # Calculate sentiment score (-1 to 1)
    total_keywords = bullish_count + bearish_count
    if total_keywords == 0:
        sentiment_score = 0
    else:
        sentiment_score = (bullish_count - bearish_count) / total_keywords
    
    # Determine sentiment category
    if sentiment_score > 0.3:
        sentiment = 'Bullish'
    elif sentiment_score < -0.3:
        sentiment = 'Bearish'
    else:
        sentiment = 'Neutral'
    
    # Identify impact factors
    impact_factors = []
    
    # Check for specific high-impact patterns
    if re.search(r'quarter|q[1-4]|results|earnings', text.lower()):
        impact_factors.append('Earnings/Results')
    if re.search(r'dividend|bonus|split', text.lower()):
        impact_factors.append('Corporate Action')
    if re.search(r'contract|deal|order|agreement', text.lower()):
        impact_factors.append('Business Development')
    if re.search(r'ceo|director|board|management', text.lower()):
        impact_factors.append('Management Changes')
    if re.search(r'stake|acquire|merge|buy', text.lower()):
        impact_factors.append('M&A Activity')
    
    return {
        'sentiment': sentiment,
        'score': sentiment_score,
        'impact_factors': impact_factors
    }

def calculate_overall_sentiment(news_items: List[Dict]) -> Dict:
    """Calculate overall sentiment from multiple news items."""
    if not news_items:
        return {
            'sentiment': 'Neutral',
            'score': 0,
            'confidence': 'Low',
            'summary': 'No recent news available'
        }
    
    # Calculate weighted average of sentiment scores
    # More recent news gets higher weight
    total_score = 0
    weights = [1.0, 0.7, 0.4]  # Weights for news items (most recent first)
    
    for i, item in enumerate(news_items[:3]):
        if i < len(weights):
            total_score += item['sentiment_score'] * weights[i]
    
    avg_score = total_score / sum(weights[:len(news_items)])
    
    # Determine overall sentiment
    if avg_score > 0.3:
        sentiment = 'Bullish'
    elif avg_score < -0.3:
        sentiment = 'Bearish'
    else:
        sentiment = 'Neutral'
    
    # Calculate confidence based on consistency of sentiment
    sentiments = [item['sentiment'] for item in news_items]
    if len(set(sentiments)) == 1:
        confidence = 'High'
    elif len(set(sentiments)) == 2:
        confidence = 'Medium'
    else:
        confidence = 'Low'
    
    # Generate summary
    impact_factors = []
    for item in news_items:
        impact_factors.extend(item['impact_factors'])
    impact_factors = list(set(impact_factors))  # Remove duplicates
    
    summary = f"{sentiment} sentiment with {confidence.lower()} confidence"
    if impact_factors:
        summary += f". Key factors: {', '.join(impact_factors[:3])}"
    
    return {
        'sentiment': sentiment,
        'score': avg_score,
        'confidence': confidence,
        'summary': summary
    }

def fetch_option_chain(symbol: str) -> Dict:
    """Fetch option chain data for a given symbol."""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        session = requests.Session()
        # Get cookies first
        session.get("https://www.nseindia.com", headers=headers)
        
        # Different URL format for indices vs stocks
        if symbol in ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]:
            url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
        else:
            url = f"https://www.nseindia.com/api/option-chain-equities?symbol={symbol}"
        
        response = session.get(url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            
            # Extract current price and timestamp
            current_price = data['records']['underlyingValue']
            timestamp = data['records']['timestamp']
            
            # Process option chain data
            option_chain = []
            for item in data['records']['data']:
                if 'CE' in item and 'PE' in item:
                    option_chain.append({
                        'strike': item['strikePrice'],
                        'call_oi': item['CE'].get('openInterest', 0),
                        'call_oi_chng': item['CE'].get('changeinOpenInterest', 0),
                        'call_volume': item['CE'].get('totalTradedVolume', 0),
                        'call_iv': item['CE'].get('impliedVolatility', 0),
                        'call_ltp': item['CE'].get('lastPrice', 0),
                        'call_chng': item['CE'].get('change', 0),
                        'call_bid': item['CE'].get('bidprice', 0),
                        'call_ask': item['CE'].get('askPrice', 0),
                        'put_oi': item['PE'].get('openInterest', 0),
                        'put_oi_chng': item['PE'].get('changeinOpenInterest', 0),
                        'put_volume': item['PE'].get('totalTradedVolume', 0),
                        'put_iv': item['PE'].get('impliedVolatility', 0),
                        'put_ltp': item['PE'].get('lastPrice', 0),
                        'put_chng': item['PE'].get('change', 0),
                        'put_bid': item['PE'].get('bidprice', 0),
                        'put_ask': item['PE'].get('askPrice', 0)
                    })
            
            return {
                'success': True,
                'symbol': symbol,
                'current_price': current_price,
                'timestamp': timestamp,
                'data': option_chain
            }
        else:
            return {
                'success': False,
                'error': f"Failed to fetch data: {response.status_code}",
                'symbol': symbol
            }
            
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'symbol': symbol
        }

def fetch_market_news() -> Dict:
    """Fetch and analyze market news from multiple sources."""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        news_items = []
        stock_mentions = {}
        
        # 1. NSE Market News
        session = requests.Session()
        session.get("https://www.nseindia.com", headers=headers)
        nse_url = "https://www.nseindia.com/api/marketStatus"
        response = session.get(nse_url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            if 'marketState' in data:
                for item in data.get('marketState', []):
                    if 'marketStatusMessage' in item:
                        news_items.append({
                            'title': item['marketStatusMessage'],
                            'source': 'NSE',
                            'date': datetime.now().strftime('%Y-%m-%d'),
                            'type': 'Market Update'
                        })
        
        # 2. MoneyControl Top News
        mc_url = "https://www.moneycontrol.com/news/business/markets/"
        response = requests.get(mc_url, headers=headers)
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            news_divs = soup.find_all('li', class_='clearfix')
            
            for news in news_divs[:10]:  # Get top 10 news items
                title_elem = news.find('h2')
                if title_elem:
                    title = title_elem.text.strip()
                    link = title_elem.find('a')['href'] if title_elem.find('a') else ''
                    date_elem = news.find('span', class_='date')
                    date = date_elem.text.strip() if date_elem else ''
                    
                    news_items.append({
                        'title': title,
                        'source': 'MoneyControl',
                        'date': date,
                        'url': link,
                        'type': 'Market News'
                    })
        
        # 3. Economic Times Markets
        et_url = "https://economictimes.indiatimes.com/markets"
        response = requests.get(et_url, headers=headers)
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            news_divs = soup.find_all('div', class_='eachStory')
            
            for news in news_divs[:10]:
                title_elem = news.find('h3')
                if title_elem:
                    title = title_elem.text.strip()
                    link = 'https://economictimes.indiatimes.com' + title_elem.find('a')['href'] if title_elem.find('a') else ''
                    
                    news_items.append({
                        'title': title,
                        'source': 'Economic Times',
                        'date': datetime.now().strftime('%Y-%m-%d'),
                        'url': link,
                        'type': 'Market News'
                    })
        
        # Process news items and extract stock mentions
        fno_stocks = fetch_fno_stocks()
        for news in news_items:
            # Analyze sentiment
            sentiment = analyze_news_sentiment(news['title'])
            news['sentiment'] = sentiment['sentiment']
            news['sentiment_score'] = sentiment['score']
            news['impact_factors'] = sentiment['impact_factors']
            
            # Find stock mentions
            words = re.findall(r'\b[A-Z]+\b', news['title'])
            for word in words:
                if word in fno_stocks:
                    if word not in stock_mentions:
                        stock_mentions[word] = []
                    stock_mentions[word].append({
                        'title': news['title'],
                        'sentiment': sentiment['sentiment'],
                        'source': news['source'],
                        'date': news['date']
                    })
        
        return {
            'success': True,
            'news': news_items,
            'stock_mentions': stock_mentions,
            'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
    except Exception as e:
        print(f"Error fetching market news: {str(e)}")
        return {
            'success': False,
            'error': str(e),
            'news': [],
            'stock_mentions': {}
        }

def get_enhanced_option_chain(symbol: str) -> Dict:
    """Get enhanced option chain with news and volume data."""
    try:
        # Fetch option chain data
        option_data = fetch_option_chain(symbol)
        if not option_data['success']:
            return option_data
        
        # Fetch volume data
        volume_data = fetch_volume_data(symbol)
        
        # Fetch stock-specific news
        news_data = fetch_stock_news(symbol)
        
        # Fetch market news and check for mentions of the symbol
        market_news = fetch_market_news()
        symbol_mentions = market_news.get('stock_mentions', {}).get(symbol, [])
        
        # Combine all news
        if news_data['success']:
            all_news = news_data['news'] + symbol_mentions
        else:
            all_news = symbol_mentions
            
        # Sort news by date (most recent first)
        all_news.sort(key=lambda x: x.get('date', ''), reverse=True)
        
        # Calculate overall sentiment including market news
        overall_sentiment = calculate_overall_sentiment(all_news)
        
        # Combine all data
        return {
            'success': True,
            'current_price': option_data['current_price'],
            'option_chain': option_data['data'],
            'volume_data': volume_data,
            'volume_signals': analyze_volume_signals(volume_data),
            'news_data': {
                'stock_news': news_data.get('news', []),
                'market_mentions': symbol_mentions,
                'overall_sentiment': overall_sentiment
            },
            'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }

def main():
    try:
        # Fetch real-time data for FnO stocks
        symbol = input("Enter the FnO stock symbol: ")
        current_price = fetch_real_time_price(symbol)
        if current_price is None:
            print("Failed to fetch real-time price.")
            return

        file_path = input("Enter the path to your option chain CSV file: ")
        options = read_option_chain(file_path)
        if not options:
            print("No valid data found.")
            return

        key_strike = find_max_put_oi_strike(options)
        calls = analyze_calls(options, current_price)
        puts = analyze_puts(options, current_price)
        otm_calls = analyze_otm_calls(options, current_price)

        print_results(key_strike, calls, puts, otm_calls)
    except Exception as e:
        print(f"An error occurred: {e}")

def fetch_fno_stocks() -> List[str]:
    """Fetch the list of stocks available for F&O trading from NSE."""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        session = requests.Session()
        # Get cookies first
        session.get("https://www.nseindia.com", headers=headers)
        
        # Fetch F&O stocks list
        url = "https://www.nseindia.com/api/equity-stock-derivatives"
        response = session.get(url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            return [stock['symbol'] for stock in data]
    except Exception as e:
        print(f"Error fetching F&O stocks list: {e}")
        return []

def fetch_real_time_price(symbol: str) -> Dict:
    """Fetch real-time price and trading data for a given F&O stock."""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        session = requests.Session()
        # Get cookies first
        session.get("https://www.nseindia.com", headers=headers)
        
        # Different URL for indices vs stocks
        if symbol in ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]:
            url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
        else:
            url = f"https://www.nseindia.com/api/option-chain-equities?symbol={symbol}"
        
        response = session.get(url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            
            # Extract relevant data
            current_price = data['records']['underlyingValue']
            timestamp = data['records']['timestamp']
            
            # Process expiry dates
            expiry_dates = sorted(list(set(item['expiryDate'] for item in data['records']['data'])))
            
            return {
                'symbol': symbol,
                'current_price': current_price,
                'timestamp': timestamp,
                'expiry_dates': expiry_dates,
                'success': True,
                'raw_data': data  # Include raw data for detailed analysis
            }
        else:
            return {
                'symbol': symbol,
                'success': False,
                'error': f"Failed to fetch data: {response.status_code}"
            }
    except Exception as e:
        return {
            'symbol': symbol,
            'success': False,
            'error': str(e)
        }

def get_option_chain(symbol: str, expiry_date: str = None) -> Dict:
    """Fetch complete option chain data for a given symbol and expiry date."""
    try:
        data = fetch_real_time_price(symbol)
        if not data['success']:
            return data
            
        raw_data = data['raw_data']
        current_price = data['current_price']
        
        # If no expiry date specified, use the nearest expiry
        if not expiry_date:
            expiry_date = data['expiry_dates'][0]
            
        # Filter data for specified expiry date
        option_chain = []
        for item in raw_data['records']['data']:
            if item['expiryDate'] == expiry_date and 'CE' in item and 'PE' in item:
                option_chain.append({
                    'strike': item['strikePrice'],
                    'call_oi': item['CE'].get('openInterest', 0),
                    'call_oi_chng': item['CE'].get('changeinOpenInterest', 0),
                    'call_volume': item['CE'].get('totalTradedVolume', 0),
                    'call_iv': item['CE'].get('impliedVolatility', 0),
                    'call_ltp': item['CE'].get('lastPrice', 0),
                    'call_chng': item['CE'].get('change', 0),
                    'call_bid': item['CE'].get('bidprice', 0),
                    'call_ask': item['CE'].get('askPrice', 0),
                    'put_oi': item['PE'].get('openInterest', 0),
                    'put_oi_chng': item['PE'].get('changeinOpenInterest', 0),
                    'put_volume': item['PE'].get('totalTradedVolume', 0),
                    'put_iv': item['PE'].get('impliedVolatility', 0),
                    'put_ltp': item['PE'].get('lastPrice', 0),
                    'put_chng': item['PE'].get('change', 0),
                    'put_bid': item['PE'].get('bidprice', 0),
                    'put_ask': item['PE'].get('askPrice', 0)
                })
                
        return {
            'symbol': symbol,
            'current_price': current_price,
            'expiry_date': expiry_date,
            'timestamp': data['timestamp'],
            'option_chain': option_chain,
            'success': True
        }
        
    except Exception as e:
        return {
            'symbol': symbol,
            'success': False,
            'error': str(e)
        }

def analyze_price_imbalances(options: List[Dict], current_price: float) -> List[Dict]:
    """Analyze price imbalances in option chain to identify trading opportunities."""
    imbalances = []
    
    for opt in options:
        # Skip if no valid prices
        if opt['call_ltp'] <= 0 or opt['put_ltp'] <= 0:
            continue
            
        # Calculate price ratios and imbalances
        strike_distance = abs(opt['strike'] - current_price)
        strike_distance_pct = strike_distance / current_price
        
        # Skip strikes too far from current price (>7%)
        if strike_distance_pct > 0.07:
            continue
            
        call_put_ratio = opt['call_ltp'] / opt['put_ltp']
        
        # Check for significant volume
        min_volume = 500
        if opt['call_volume'] < min_volume or opt['put_volume'] < min_volume:
            continue
            
        # Calculate bid-ask spreads
        call_spread = opt['call_ask'] - opt['call_bid']
        put_spread = opt['put_ask'] - opt['put_bid']
        
        # Skip if spreads are too wide (>5% of option price)
        if (call_spread / opt['call_ltp'] > 0.05 or 
            put_spread / opt['put_ltp'] > 0.05):
            continue
            
        # Look for price imbalances
        if call_put_ratio > 1.5:  # Calls relatively expensive
            score = min((call_put_ratio - 1.5) * 10, 10)  # Score from 0-10
            imbalances.append({
                'type': 'PUT',
                'strike': opt['strike'],
                'buy_price': opt['put_ask'],
                'exit': opt['put_ask'] * 1.5,
                'stop_loss': opt['put_bid'] * 0.7,
                'score': score,
                'reason': f"Calls expensive relative to puts (ratio: {call_put_ratio:.2f})"
            })
        elif call_put_ratio < 0.67:  # Puts relatively expensive
            score = min((0.67 / call_put_ratio - 1) * 10, 10)  # Score from 0-10
            imbalances.append({
                'type': 'CALL',
                'strike': opt['strike'],
                'buy_price': opt['call_ask'],
                'exit': opt['call_ask'] * 1.5,
                'stop_loss': opt['call_bid'] * 0.7,
                'score': score,
                'reason': f"Puts expensive relative to calls (ratio: {call_put_ratio:.2f})"
            })
    
    # Sort by score
    imbalances.sort(key=lambda x: x['score'], reverse=True)
    return imbalances

if __name__ == "__main__":
    main()