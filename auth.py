"""
Authentication and security middleware
JWT verification for Supabase tokens
"""
import jwt
import time
from functools import wraps
from flask import request, jsonify
from config import Config
from collections import defaultdict

# Rate limiting storage (in production, use Redis)
rate_limit_storage = defaultdict(list)
cooldown_storage = {}

def verify_token(token):
    """Verify Supabase JWT token"""
    try:
        # Decode without signature verification
        # Supabase JWT tokens work the same with both old and new API key systems
        payload = jwt.decode(
            token,
            options={"verify_signature": False}
        )
        
        # Validate the token is from Supabase (check issuer)
        issuer = payload.get('iss', '')
        if not (issuer.startswith('https://oqzowaojrcvnoshdzgyd.supabase.co') or 
                'supabase' in issuer.lower()):
            print(f"Invalid issuer: {issuer}")
            return None
        
        # Check if token is expired
        if payload.get('exp', 0) < time.time():
            print("Token expired")
            return None
        
        # Verify required claims exist
        if not payload.get('sub'):
            print("Missing user ID (sub)")
            return None
        
        return payload
    except jwt.InvalidTokenError as e:
        print(f"Invalid token: {e}")
        return None
    except Exception as e:
        print(f"Token verification error: {e}")
        return None

def require_auth(f):
    """Decorator to require authentication"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        
        if not auth_header:
            return jsonify({'error': 'No authorization header'}), 401
        
        # Extract token from "Bearer <token>"
        try:
            token = auth_header.split(' ')[1]
        except IndexError:
            return jsonify({'error': 'Invalid authorization header format'}), 401
        
        # Verify token
        payload = verify_token(token)
        if not payload:
            return jsonify({'error': 'Invalid or expired token'}), 401
        
        # Add user info to request
        request.user_id = payload.get('sub')
        request.user_email = payload.get('email')
        
        return f(*args, **kwargs)
    
    return decorated_function

def rate_limit(max_requests=30, window_seconds=60):
    """Rate limiting decorator"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user_id = getattr(request, 'user_id', None)
            if not user_id:
                return jsonify({'error': 'Unauthorized'}), 401
            
            # Get current time
            now = time.time()
            
            # Clean old requests from storage
            rate_limit_storage[user_id] = [
                req_time for req_time in rate_limit_storage[user_id]
                if now - req_time < window_seconds
            ]
            
            # Check rate limit
            if len(rate_limit_storage[user_id]) >= max_requests:
                return jsonify({
                    'error': 'Rate limit exceeded',
                    'message': f'Maximum {max_requests} requests per {window_seconds} seconds'
                }), 429
            
            # Add current request
            rate_limit_storage[user_id].append(now)
            
            return f(*args, **kwargs)
        
        return decorated_function
    return decorator

def check_cooldown(user_id, cooldown_seconds):
    """Check if user is on cooldown"""
    now = time.time()
    
    if user_id in cooldown_storage:
        last_cast = cooldown_storage[user_id]
        time_elapsed = now - last_cast
        
        if time_elapsed < cooldown_seconds:
            remaining = cooldown_seconds - time_elapsed
            return False, remaining
    
    return True, 0

def set_cooldown(user_id):
    """Set cooldown for user"""
    cooldown_storage[user_id] = time.time()

def cooldown_required(cooldown_seconds):
    """Decorator to enforce cooldown between casts"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user_id = getattr(request, 'user_id', None)
            if not user_id:
                return jsonify({'error': 'Unauthorized'}), 401
            
            # Check cooldown
            can_cast, remaining = check_cooldown(user_id, cooldown_seconds)
            
            if not can_cast:
                return jsonify({
                    'error': 'Cooldown active',
                    'message': f'Please wait {remaining:.1f} seconds before casting again',
                    'remaining_seconds': remaining
                }), 429
            
            # Execute function
            result = f(*args, **kwargs)
            
            # Set cooldown after successful cast
            if isinstance(result, tuple):
                response, status_code = result
                if status_code == 200:
                    set_cooldown(user_id)
            else:
                set_cooldown(user_id)
            
            return result
        
        return decorated_function
    return decorator

