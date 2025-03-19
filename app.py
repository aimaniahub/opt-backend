from flask import Flask, render_template, request, jsonify
import os
import requests
import pandas as pd
import io
from werkzeug.utils import secure_filename
from trade import (read_option_chain, find_max_put_oi_strike, analyze_calls, analyze_puts, 
                  analyze_otm_calls, analyze_market_direction, analyze_best_trades, 
                  analyze_price_imbalances, fetch_fno_stocks, fetch_volume_data, 
                  analyze_volume_signals, get_enhanced_option_chain, fetch_market_news)

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Ensure upload folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

ALLOWED_EXTENSIONS = {'csv'}

# Add indices
INDICES = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]

def get_fno_stocks():
    """Fetch current F&O stocks list from NSE"""
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
            # Extract symbols from the response
            fno_stocks = [stock['symbol'] for stock in data]
            return sorted(fno_stocks)
        else:
            # Fallback to a basic list if API fails
            return [
                "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "HDFC", "KOTAKBANK",
                "LT", "AXISBANK", "SBIN", "BHARTIARTL", "ITC", "HCLTECH", "TITAN",
                "BAJFINANCE", "ASIANPAINT", "MARUTI", "SUNPHARMA", "TATAMOTORS",
                "ULTRACEMCO", "WIPRO", "HINDUNILVR", "ADANIENT", "TATASTEEL", "BAJAJFINSV",
                "M&M", "TECHM", "POWERGRID", "NTPC", "ONGC", "GRASIM", "HINDALCO",
                "JSWSTEEL", "APOLLOHOSP", "CIPLA", "EICHERMOT", "COALINDIA", "DRREDDY",
                "BPCL", "UPL"
            ]
    except Exception as e:
        print(f"Error fetching F&O stocks: {e}")
        # Return a basic list as fallback
        return [
            "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "HDFC", "KOTAKBANK",
            "LT", "AXISBANK", "SBIN", "BHARTIARTL", "ITC", "HCLTECH", "TITAN",
            "BAJFINANCE", "ASIANPAINT", "MARUTI", "SUNPHARMA", "TATAMOTORS",
            "ULTRACEMCO", "WIPRO", "HINDUNILVR", "ADANIENT", "TATASTEEL", "BAJAJFINSV"
        ]

# Get F&O stocks list once at the start
FNO_STOCKS = get_fno_stocks()

# Combined list for dropdown
SYMBOLS = INDICES + FNO_STOCKS

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/')
def index():
    # Refresh F&O stocks list when loading the page
    global FNO_STOCKS, SYMBOLS
    FNO_STOCKS = get_fno_stocks()
    SYMBOLS = INDICES + FNO_STOCKS
    return render_template('index.html', symbols=SYMBOLS)

@app.route('/get_current_price', methods=['POST'])
def get_current_price():
    symbol = request.json.get('symbol')
    if not symbol:
        return jsonify({'error': 'Symbol is required'}), 400
    
    try:
        # For demonstration, we'll use a simple API to get the current price
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        # Different URL format for indices vs stocks
        if symbol in INDICES:
            url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
        else:
            url = f"https://www.nseindia.com/api/option-chain-equities?symbol={symbol}"
            
        # This is a simplified example - in production you'd need proper session handling
        session = requests.Session()
        session.get("https://www.nseindia.com", headers=headers)  # Get cookies
        response = session.get(url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            current_price = data['records']['underlyingValue']
            return jsonify({'current_price': current_price})
        else:
            return jsonify({'error': f'Failed to fetch data: {response.status_code}'}), 500
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/fetch_option_chain', methods=['POST'])
def fetch_option_chain():
    symbol = request.json.get('symbol')
    if not symbol:
        return jsonify({'error': 'Symbol is required'}), 400
    
    try:
        # Get enhanced option chain with volume data and news
        option_data = get_enhanced_option_chain(symbol)
        
        if not option_data['success']:
            return jsonify({'error': option_data.get('error', 'Failed to fetch data')}), 500
            
        current_price = option_data['current_price']
        option_chain_data = option_data['option_chain']
        volume_signals = option_data['volume_signals']
        volume_data = option_data['volume_data']
        news_data = option_data['news_data']
        
        # Get market news for broader context
        market_news = fetch_market_news()
        
        # Get the sector for the stock
        sector = get_stock_sector(symbol)
        
        # Analyze the data
        market_direction = analyze_market_direction(option_chain_data, current_price)
        
        # Get all possible trades first
        all_possible_trades = []
        
        # Process each strike price for valid trades
        for opt in option_chain_data:
            strike_price = opt['strike']
            price_diff_pct = (strike_price - current_price) / current_price
            
            # Check CALL options
            if (opt['call_volume'] > 0 and opt['call_oi'] > 0 and 
                opt['call_ltp'] > 0 and abs(price_diff_pct) <= 0.10):
                
                call_spread = opt['call_ask'] - opt['call_bid']
                call_spread_pct = call_spread / opt['call_ltp'] if opt['call_ltp'] > 0 else float('inf')
                
                if call_spread_pct < 0.10:
                    trade = {
                        'type': 'CALL',
                        'strike': strike_price,
                        'buy_price': opt['call_ask'],
                        'current_price': opt['call_ltp'],
                        'exit': opt['call_ask'] * 1.5,
                        'stop_loss': opt['call_bid'] * 0.7,
                        'oi': opt['call_oi'],
                        'oi_change': opt['call_oi_chng'],
                        'volume': opt['call_volume'],
                        'iv': opt['call_iv'],
                        'distance': f"{price_diff_pct * 100:.1f}%",
                        'spread': f"{call_spread_pct * 100:.1f}%"
                    }
                    all_possible_trades.append(trade)
            
            # Check PUT options with similar logic
            if (opt['put_volume'] > 0 and opt['put_oi'] > 0 and 
                opt['put_ltp'] > 0 and abs(price_diff_pct) <= 0.10):
                
                put_spread = opt['put_ask'] - opt['put_bid']
                put_spread_pct = put_spread / opt['put_ltp'] if opt['put_ltp'] > 0 else float('inf')
                
                if put_spread_pct < 0.10:
                    trade = {
                        'type': 'PUT',
                        'strike': strike_price,
                        'buy_price': opt['put_ask'],
                        'current_price': opt['put_ltp'],
                        'exit': opt['put_ask'] * 1.5,
                        'stop_loss': opt['put_bid'] * 0.7,
                        'oi': opt['put_oi'],
                        'oi_change': opt['put_oi_chng'],
                        'volume': opt['put_volume'],
                        'iv': opt['put_iv'],
                        'distance': f"{price_diff_pct * 100:.1f}%",
                        'spread': f"{put_spread_pct * 100:.1f}%"
                    }
                    all_possible_trades.append(trade)
        
        # Get the best trades using existing analysis
        best_trades = analyze_best_trades(option_chain_data, current_price, volume_signals)
        imbalance_trades = analyze_price_imbalances(option_chain_data, current_price)
        
        # Initialize lists for different types of trades
        high_potential_trades = []
        
        # Safely extend high_potential_trades with available trades
        if best_trades and 'best_overall' in best_trades and best_trades['best_overall']:
            high_potential_trades.extend(best_trades['best_overall'])
        if best_trades and 'best_atm' in best_trades and best_trades['best_atm']:
            high_potential_trades.extend(best_trades['best_atm'])
        if best_trades and 'best_otm' in best_trades and best_trades['best_otm']:
            high_potential_trades.extend(best_trades['best_otm'])
        if imbalance_trades:
            high_potential_trades.extend(imbalance_trades[:2])
        
        # Sort high potential trades if we have any
        if high_potential_trades:
            high_potential_trades.sort(key=lambda x: x['score'], reverse=True)
            
            # Get trades aligned with market direction, news sentiment, and sector trend
            sector_sentiment = 'Neutral'
            if market_news['success']:
                sector_sentiment = market_news['sector_sentiment']
            
            high_potential_trades.append({
                'type': 'SECTOR SENTIMENT',
                'sentiment': sector_sentiment
            })
        
        # Prepare results for front end
        results = {
            'symbol': symbol,
            'current_price': current_price,
            'option_chain_data': option_chain_data,
            'market_direction': market_direction,
            'high_potential_trades': high_potential_trades,
            'volume_signals': volume_signals,
            'volume_data': volume_data,
            'news_data': news_data,
            'market_news': market_news
        }
        
        return jsonify(results)

    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))  # Use Render's PORT variable
    app.run(host='0.0.0.0', port=port)
