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

# Get F&O stocks list
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
        # In production, you should use a reliable market data API
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
                sector_data = next((s for s in market_news.get('sector_analysis', {}).values() 
                                  if symbol in s.get('stocks_mentioned', [])), None)
                if sector_data:
                    bullish = sector_data['bullish_count']
                    bearish = sector_data['bearish_count']
                    if bullish > bearish:
                        sector_sentiment = 'Bullish'
                    elif bearish > bullish:
                        sector_sentiment = 'Bearish'
            
            aligned_trades = [t for t in high_potential_trades if 
                            (t['type'] == 'CALL' and market_direction['bias'] == 'Bullish' and 
                             news_data['overall_sentiment']['sentiment'] != 'Bearish' and
                             sector_sentiment != 'Bearish') or
                            (t['type'] == 'PUT' and market_direction['bias'] == 'Bearish' and 
                             news_data['overall_sentiment']['sentiment'] != 'Bullish' and
                             sector_sentiment != 'Bullish')]
            
            # Select top trade with fallback
            top_trade = aligned_trades[0] if aligned_trades else high_potential_trades[0]
            
            # Add comprehensive recommendation
            if top_trade:
                news_context = f" News: {news_data['overall_sentiment']['summary']}"
                sector_context = f" Sector: {sector} sentiment is {sector_sentiment}"
                market_context = f" Market: {market_direction['bias']} with {market_direction['confidence']}% confidence"
                
                top_trade['recommendation'] = (
                    f"BEST TRADE: {top_trade['type']} at strike ₹{top_trade['strike']}. "
                    f"{market_context}.{sector_context}.{news_context}"
                )
        else:
            # No high potential trades found
            top_trade = {
                'type': 'NONE',
                'recommendation': 'No high-potential trades found',
                'reason': 'Consider checking all available trades below'
            }
        
        # Prepare response with all available data
        response = {
            'symbol': symbol,
            'current_price': current_price,
            'market_direction': market_direction,
            'sector': sector,
            'volume_signals': volume_signals or {'volume_signal': 'Neutral', 'reasons': []},
            'volume_data': {
                'inflow_ratio': volume_data.get('inflow_ratio', 0),
                'outflow_ratio': volume_data.get('outflow_ratio', 0),
                'delivery_percentage': volume_data.get('delivery_percentage', 0),
                'signal': volume_signals.get('volume_signal', 'Neutral'),
                'reasons': volume_signals.get('reasons', [])
            } if volume_data else {},
            'news_data': {
                'stock_news': news_data.get('stock_news', []),
                'market_mentions': news_data.get('market_mentions', []),
                'overall_sentiment': news_data.get('overall_sentiment', {
                    'sentiment': 'Neutral',
                    'confidence': 'Low',
                    'summary': 'No recent news available'
                })
            },
            'market_context': {
                'sector_sentiment': sector_sentiment,
                'sector_stocks': market_news.get('sector_analysis', {}).get(sector, {}).get('stocks_mentioned', [])
            } if market_news.get('success') else {},
            'best_trade': top_trade,
            'best_trades': best_trades or {'best_overall': [], 'best_atm': [], 'best_otm': []},
            'imbalance_trades': imbalance_trades[:3] if imbalance_trades else [],
            'all_trades': sorted(all_possible_trades, key=lambda x: abs(float(x['distance'].rstrip('%'))))
        }
        
        return jsonify(response)
        
    except Exception as e:
        return jsonify({
            'error': str(e),
            'symbol': symbol,
            'best_trade': {
                'type': 'ERROR',
                'recommendation': 'Failed to analyze trades',
                'reason': str(e)
            },
            'news_data': {
                'stock_news': [],
                'market_mentions': [],
                'overall_sentiment': {
                    'sentiment': 'Neutral',
                    'confidence': 'Low',
                    'summary': 'Failed to fetch news'
                }
            },
            'all_trades': []
        }), 500

@app.route('/analyze', methods=['POST'])
def analyze():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded. Please upload a file from your device.'}), 400
    
    file = request.files['file']
    current_price = request.form.get('currentPrice')
    
    if not current_price:
        return jsonify({'error': 'Current price is required'}), 400
    
    try:
        current_price = float(current_price)
    except ValueError:
        return jsonify({'error': 'Invalid current price value'}), 400
    
    if file.filename == '':
        return jsonify({'error': 'No file selected. Please upload a file from your device.'}), 400
    
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        try:
            options = read_option_chain(filepath)
            if not options:
                return jsonify({'error': 'No valid data found in file'}), 400

            market_direction = analyze_market_direction(options, current_price)
            best_trades = analyze_best_trades(options, current_price)
            imbalance_trades = analyze_price_imbalances(options, current_price)

            # Clean up the uploaded file
            os.remove(filepath)

            # Find the absolute best trade based on scores
            all_trades = []
            if best_trades['best_overall']:
                all_trades.extend(best_trades['best_overall'])
            if best_trades['best_atm']:
                all_trades.extend(best_trades['best_atm'])
            if best_trades['best_otm']:
                all_trades.extend(best_trades['best_otm'])
            if imbalance_trades:
                all_trades.extend(imbalance_trades[:2])
                
            all_trades.sort(key=lambda x: x['score'], reverse=True)
            
            # Get the top trade that aligns with market direction
            aligned_trades = [t for t in all_trades if 
                             (t['type'] == 'CALL' and market_direction['bias'] == 'Bullish') or
                             (t['type'] == 'PUT' and market_direction['bias'] == 'Bearish')]
            
            # If we have aligned trades, prioritize them
            top_trade = aligned_trades[0] if aligned_trades else all_trades[0] if all_trades else None
            
            # Add a clear recommendation if we have a top trade
            if top_trade:
                top_trade['recommendation'] = f"BEST TRADE: {top_trade['type']} at strike ₹{top_trade['strike']}"

            return jsonify({
                'current_price': current_price,
                'market_direction': market_direction,
                'best_trade': top_trade,
                'best_trades': best_trades,
                'imbalance_trades': imbalance_trades[:3]
            })

        except Exception as e:
            return jsonify({'error': str(e)}), 500
        
    return jsonify({'error': 'Invalid file type'}), 400

@app.route('/fetch_market_news', methods=['GET'])
def fetch_market_news():
    try:
        # Fetch market news data
        market_news = fetch_market_news()
        
        if not market_news['success']:
            return jsonify({
                'error': market_news.get('error', 'Failed to fetch market news'),
                'news': [],
                'stock_mentions': {},
                'sector_analysis': {}
            }), 500
            
        # Group news by sector for sector-specific analysis
        sector_analysis = {}
        for stock, mentions in market_news['stock_mentions'].items():
            # Get the sector for the stock (you would need to maintain a sector mapping)
            sector = get_stock_sector(stock)
            if sector not in sector_analysis:
                sector_analysis[sector] = {
                    'bullish_count': 0,
                    'bearish_count': 0,
                    'neutral_count': 0,
                    'stocks_mentioned': set()
                }
            
            sector_analysis[sector]['stocks_mentioned'].add(stock)
            for mention in mentions:
                if mention['sentiment'] == 'Bullish':
                    sector_analysis[sector]['bullish_count'] += 1
                elif mention['sentiment'] == 'Bearish':
                    sector_analysis[sector]['bearish_count'] += 1
                else:
                    sector_analysis[sector]['neutral_count'] += 1
        
        # Convert sets to lists for JSON serialization
        for sector in sector_analysis:
            sector_analysis[sector]['stocks_mentioned'] = list(sector_analysis[sector]['stocks_mentioned'])
        
        return jsonify({
            'success': True,
            'news': market_news['news'],
            'stock_mentions': market_news['stock_mentions'],
            'sector_analysis': sector_analysis,
            'last_updated': market_news['last_updated']
        })
        
    except Exception as e:
        return jsonify({
            'error': str(e),
            'news': [],
            'stock_mentions': {},
            'sector_analysis': {}
        }), 500

def get_stock_sector(symbol: str) -> str:
    """Get the sector for a given stock symbol."""
    # This is a simplified sector mapping. In production, you would want to maintain
    # a complete mapping or fetch it from a reliable source
    sector_mapping = {
        'RELIANCE': 'Oil & Gas',
        'TCS': 'IT',
        'INFY': 'IT',
        'HDFCBANK': 'Banking',
        'ICICIBANK': 'Banking',
        'HDFC': 'Banking',
        'KOTAKBANK': 'Banking',
        'LT': 'Infrastructure',
        'AXISBANK': 'Banking',
        'SBIN': 'Banking',
        'BHARTIARTL': 'Telecom',
        'ITC': 'FMCG',
        'HCLTECH': 'IT',
        'TITAN': 'Consumer Goods',
        'BAJFINANCE': 'Financial Services',
        'ASIANPAINT': 'Chemicals',
        'MARUTI': 'Auto',
        'SUNPHARMA': 'Pharma',
        'TATAMOTORS': 'Auto'
    }
    return sector_mapping.get(symbol, 'Others')

if __name__ == '__main__':
    app.run(debug=True) 