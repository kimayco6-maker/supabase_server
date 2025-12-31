"""
Fishing Game Backend API
Secure server-side game logic with Supabase (NEW API Key System)
"""
from flask import Flask, request, jsonify
from flask_cors import CORS
from supabase import create_client, ClientOptions
from config import Config
from auth import require_auth, rate_limit, cooldown_required
from game_logic import FishingGame
import traceback

# Initialize Flask app
app = Flask(__name__)
app.config.from_object(Config)

# Enable CORS for frontend
CORS(app, resources={
    r"/api/*": {
        "origins": ["*"],  # In production, specify your frontend domain
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"]
    }
})

# Validate configuration
try:
    Config.validate()
except ValueError as e:
    print(f"Configuration error: {e}")
    print("Please set required environment variables")
    exit(1)

# Initialize Supabase client with NEW API key system
print(f"Initializing Supabase with URL: {Config.SUPABASE_URL}")
print(f"Secret key starts with: {Config.SUPABASE_SECRET_KEY[:20]}...")

# Create client with NEW secret key
options = ClientOptions(
    auto_refresh_token=False,
    persist_session=False,
    schema="public"
)
supabase = create_client(
    Config.SUPABASE_URL,
    Config.SUPABASE_SECRET_KEY,
    options=options
)

# Test connection
try:
    print("Testing Supabase connection...")
    test_response = supabase.table('fish_species').select('id').limit(1).execute()
    print(f"✓ Connected to Supabase: {len(test_response.data) if test_response.data else 0} records found")
except Exception as e:
    print(f"✗ Supabase connection test failed: {e}")

# Initialize game logic
game = FishingGame(supabase)

# ============================================
# HEALTH CHECK
# ============================================

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'service': 'fishing-game-api',
        'version': '2.0-new-api-keys'
    }), 200

# ============================================
# AUTHENTICATION ENDPOINTS
# ============================================

@app.route('/api/auth/signup', methods=['POST'])
def signup():
    """Create new player account"""
    try:
        data = request.get_json()
        email = data.get('email')
        password = data.get('password')
        username = data.get('username')
        
        if not email or not password:
            return jsonify({'error': 'Email and password required'}), 400
        
        print(f"Creating account for: {email}")
        
        # Create auth user
        response = supabase.auth.sign_up({
            'email': email,
            'password': password,
            'options': {
                'data': {
                    'username': username or email.split('@')[0]
                }
            }
        })
        
        if response.user:
            print(f"✓ Account created: {response.user.id}")
            return jsonify({
                'message': 'Account created successfully',
                'user': {
                    'id': response.user.id,
                    'email': response.user.email
                }
            }), 201
        else:
            print("✗ No user in response")
            return jsonify({'error': 'Failed to create account'}), 400
            
    except Exception as e:
        print(f"Signup error: {type(e).__name__}: {e}")
        traceback.print_exc()
        return jsonify({'error': f'{type(e).__name__}: {str(e)}'}), 500

@app.route('/api/auth/login', methods=['POST'])
def login():
    """Login player"""
    try:
        data = request.get_json()
        email = data.get('email')
        password = data.get('password')
        
        if not email or not password:
            return jsonify({'error': 'Email and password required'}), 400
        
        # Sign in
        response = supabase.auth.sign_in_with_password({
            'email': email,
            'password': password
        })
        
        if response.session:
            return jsonify({
                'message': 'Login successful',
                'session': {
                    'access_token': response.session.access_token,
                    'refresh_token': response.session.refresh_token
                },
                'user': {
                    'id': response.user.id,
                    'email': response.user.email
                }
            }), 200
        else:
            return jsonify({'error': 'Invalid credentials'}), 401
            
    except Exception as e:
        print(f"Login error: {e}")
        return jsonify({'error': str(e)}), 400

# ============================================
# GAME ENDPOINTS (Require Authentication)
# ============================================

@app.route('/api/cast', methods=['POST'])
@require_auth
@rate_limit(max_requests=Config.MAX_REQUESTS_PER_MINUTE, window_seconds=60)
@cooldown_required(cooldown_seconds=Config.CAST_COOLDOWN_SECONDS)
def cast_line():
    """
    Main game action: Cast fishing line
    Server-side fish generation and saving
    """
    try:
        player_id = request.user_id
        
        # Generate fish (server-side only)
        result = game.cast_line(player_id)
        
        if not result.success:
            return jsonify({'error': result.message}), 500
        
        # Return catch details
        return jsonify({
            'success': True,
            'message': result.message,
            'catch': {
                'fish': {
                    'id': result.fish['id'],
                    'name': result.fish['name'],
                    'rarity': result.fish['rarity'],
                    'description': result.fish['description'],
                    'image_url': result.fish.get('image_url')
                },
                'weight': result.weight,
                'points': result.points,
                'is_personal_best': result.is_personal_best
            }
        }), 200
        
    except Exception as e:
        print(f"Cast error: {e}")
        print(traceback.format_exc())
        return jsonify({'error': 'Failed to cast line'}), 500

@app.route('/api/inventory', methods=['GET'])
@require_auth
@rate_limit(max_requests=100, window_seconds=60)
def get_inventory():
    """Get player's catch inventory"""
    try:
        player_id = request.user_id
        limit = request.args.get('limit', 50, type=int)
        
        catches = game.get_player_catches(player_id, limit=limit)
        
        return jsonify({
            'success': True,
            'catches': catches,
            'count': len(catches)
        }), 200
        
    except Exception as e:
        print(f"Inventory error: {e}")
        return jsonify({'error': 'Failed to load inventory'}), 500

@app.route('/api/player/stats', methods=['GET'])
@require_auth
@rate_limit(max_requests=100, window_seconds=60)
def get_player_stats():
    """Get player statistics"""
    try:
        player_id = request.user_id
        
        stats = game.get_player_stats(player_id)
        
        if not stats:
            return jsonify({'error': 'Player not found'}), 404
        
        return jsonify({
            'success': True,
            'stats': stats
        }), 200
        
    except Exception as e:
        print(f"Stats error: {e}")
        return jsonify({'error': 'Failed to load stats'}), 500

@app.route('/api/fish-species', methods=['GET'])
@require_auth
@rate_limit(max_requests=100, window_seconds=60)
def get_fish_species():
    """Get all fish species (for fish collection book)"""
    try:
        fish_list = game.get_all_fish_species()
        
        return jsonify({
            'success': True,
            'fish_species': fish_list,
            'count': len(fish_list)
        }), 200
        
    except Exception as e:
        print(f"Fish species error: {e}")
        return jsonify({'error': 'Failed to load fish species'}), 500

# ============================================
# LEADERBOARD ENDPOINTS
# ============================================

@app.route('/api/leaderboard/heaviest', methods=['GET'])
@require_auth
@rate_limit(max_requests=100, window_seconds=60)
def leaderboard_heaviest():
    """Get heaviest fish leaderboard"""
    try:
        limit = request.args.get('limit', 100, type=int)
        leaderboard = game.get_leaderboard_heaviest(limit=limit)
        
        return jsonify({
            'success': True,
            'leaderboard': leaderboard,
            'count': len(leaderboard)
        }), 200
        
    except Exception as e:
        print(f"Leaderboard error: {e}")
        return jsonify({'error': 'Failed to load leaderboard'}), 500

@app.route('/api/leaderboard/most-catches', methods=['GET'])
@require_auth
@rate_limit(max_requests=100, window_seconds=60)
def leaderboard_most_catches():
    """Get most catches leaderboard"""
    try:
        limit = request.args.get('limit', 100, type=int)
        leaderboard = game.get_leaderboard_most_catches(limit=limit)
        
        return jsonify({
            'success': True,
            'leaderboard': leaderboard,
            'count': len(leaderboard)
        }), 200
        
    except Exception as e:
        print(f"Leaderboard error: {e}")
        return jsonify({'error': 'Failed to load leaderboard'}), 500

@app.route('/api/leaderboard/rare-catches', methods=['GET'])
@require_auth
@rate_limit(max_requests=100, window_seconds=60)
def leaderboard_rare_catches():
    """Get rare catches leaderboard"""
    try:
        limit = request.args.get('limit', 100, type=int)
        leaderboard = game.get_leaderboard_rare_catches(limit=limit)
        
        return jsonify({
            'success': True,
            'leaderboard': leaderboard,
            'count': len(leaderboard)
        }), 200
        
    except Exception as e:
        print(f"Leaderboard error: {e}")
        return jsonify({'error': 'Failed to load leaderboard'}), 500

# ============================================
# ERROR HANDLERS
# ============================================

@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Endpoint not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({'error': 'Internal server error'}), 500

# ============================================
# RUN SERVER
# ============================================

if __name__ == '__main__':
    print("🎣 Starting Fishing Game API Server...")
    print(f"📡 Supabase URL: {Config.SUPABASE_URL}")
    print(f"⏱️  Cast cooldown: {Config.CAST_COOLDOWN_SECONDS}s")
    print(f"🚦 Rate limit: {Config.MAX_REQUESTS_PER_MINUTE} requests/minute")
    app.run(host='0.0.0.0', port=5000, debug=Config.DEBUG)

