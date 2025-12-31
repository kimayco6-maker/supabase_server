"""
Authentication and security middleware
JWT verification for Supabase tokens with proper security
"""
import jwt
import time
import threading
import hashlib
from functools import wraps
from flask import request, jsonify
from config import Config
from collections import defaultdict

# Thread-safe storage using locks
_rate_limit_lock = threading.Lock()
_cooldown_lock = threading.Lock()
_blacklist_lock = threading.Lock()

# Rate limiting storage (in production, use Redis)
rate_limit_storage = defaultdict(list)
cooldown_storage = {}

# Token blacklist for logout (in production, use Redis with TTL)
token_blacklist = set()

# Login attempt tracking for brute force protection
login_attempts = defaultdict(list)
LOGIN_MAX_ATTEMPTS = 5
LOGIN_LOCKOUT_SECONDS = 300  # 5 minutes


def get_token_hash(token: str) -> str:
    """Get a hash of the token for blacklist storage"""
    return hashlib.sha256(token.encode()).hexdigest()[:32]


def blacklist_token(token: str):
    """Add token to blacklist (for logout)"""
    with _blacklist_lock:
        token_hash = get_token_hash(token)
        token_blacklist.add(token_hash)


def is_token_blacklisted(token: str) -> bool:
    """Check if token is blacklisted"""
    with _blacklist_lock:
        token_hash = get_token_hash(token)
        return token_hash in token_blacklist


def verify_token(token: str):
    """
    Verify Supabase JWT token with PROPER signature verification
    """
    try:
        # First check if token is blacklisted (logged out)
        if is_token_blacklisted(token):
            print("Token is blacklisted (logged out)")
            return None
        
        # Get the JWT secret from config
        jwt_secret = Config.SUPABASE_JWT_SECRET
        
        if jwt_secret:
            # PROPER verification with signature check
            try:
                payload = jwt.decode(
                    token,
                    jwt_secret,
                    algorithms=["HS256"],  # Only allow HS256, block 'none' attack
                    options={
                        "verify_signature": True,
                        "verify_exp": True,
                        "verify_iat": True,
                        "require": ["exp", "sub", "iss"]
                    }
                )
            except jwt.InvalidAlgorithmError:
                print("Invalid algorithm - possible algorithm confusion attack")
                return None
            except jwt.InvalidSignatureError:
                print("Invalid signature - token was modified")
                return None
        else:
            # Fallback: Decode and verify claims manually (less secure)
            # This should only be used if JWT_SECRET is not available
            print("WARNING: JWT_SECRET not configured, using limited verification")
            
            # First, check the algorithm in header
            try:
                header = jwt.get_unverified_header(token)
                if header.get('alg', '').lower() == 'none':
                    print("Rejected: 'none' algorithm not allowed")
                    return None
                if header.get('alg') not in ['HS256', 'RS256', 'ES256']:
                    print(f"Rejected: Unsupported algorithm {header.get('alg')}")
                    return None
            except:
                return None
            
            payload = jwt.decode(
                token,
                options={"verify_signature": False}
            )
        
        # Validate the token is from Supabase (check issuer)
        issuer = payload.get('iss', '')
        expected_issuer = Config.SUPABASE_URL
        
        if not issuer or expected_issuer not in issuer:
            print(f"Invalid issuer: {issuer}, expected: {expected_issuer}")
            return None
        
        # Check if token is expired
        exp = payload.get('exp', 0)
        if exp < time.time():
            print("Token expired")
            return None
        
        # Verify required claims exist
        if not payload.get('sub'):
            print("Missing user ID (sub)")
            return None
        
        return payload
        
    except jwt.ExpiredSignatureError:
        print("Token has expired")
        return None
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
            parts = auth_header.split(' ')
            if len(parts) != 2 or parts[0].lower() != 'bearer':
                return jsonify({'error': 'Invalid authorization header format'}), 401
            token = parts[1]
        except IndexError:
            return jsonify({'error': 'Invalid authorization header format'}), 401
        
        # Verify token
        payload = verify_token(token)
        if not payload:
            return jsonify({'error': 'Invalid or expired token'}), 401
        
        # Add user info to request (ONLY from verified JWT, never from request body)
        request.user_id = payload.get('sub')
        request.user_email = payload.get('email')
        request.token = token  # Store for potential logout
        
        return f(*args, **kwargs)
    
    return decorated_function


def check_login_attempts(identifier: str) -> tuple:
    """Check if login attempts are within limits"""
    now = time.time()
    
    with _rate_limit_lock:
        # Clean old attempts
        login_attempts[identifier] = [
            attempt_time for attempt_time in login_attempts[identifier]
            if now - attempt_time < LOGIN_LOCKOUT_SECONDS
        ]
        
        # Check if locked out
        if len(login_attempts[identifier]) >= LOGIN_MAX_ATTEMPTS:
            oldest_attempt = min(login_attempts[identifier])
            remaining = LOGIN_LOCKOUT_SECONDS - (now - oldest_attempt)
            return False, remaining
        
        return True, 0


def record_login_attempt(identifier: str):
    """Record a failed login attempt"""
    with _rate_limit_lock:
        login_attempts[identifier].append(time.time())


def clear_login_attempts(identifier: str):
    """Clear login attempts after successful login"""
    with _rate_limit_lock:
        login_attempts[identifier] = []


def login_rate_limit(f):
    """Rate limiting specifically for login endpoint"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Get identifier (IP + email if available)
        client_ip = request.remote_addr or 'unknown'
        data = request.get_json() or {}
        email = data.get('email', '')
        
        # Check both IP and email-based rate limits
        ip_allowed, ip_remaining = check_login_attempts(f"ip:{client_ip}")
        email_allowed, email_remaining = check_login_attempts(f"email:{email}") if email else (True, 0)
        
        if not ip_allowed:
            return jsonify({
                'error': 'Too many login attempts',
                'message': f'Please wait {ip_remaining:.0f} seconds before trying again',
                'retry_after': ip_remaining
            }), 429
        
        if not email_allowed:
            return jsonify({
                'error': 'Too many login attempts for this account',
                'message': f'Please wait {email_remaining:.0f} seconds before trying again',
                'retry_after': email_remaining
            }), 429
        
        return f(*args, **kwargs)
    
    return decorated_function


def rate_limit(max_requests=30, window_seconds=60):
    """Rate limiting decorator with thread safety"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user_id = getattr(request, 'user_id', None)
            if not user_id:
                return jsonify({'error': 'Unauthorized'}), 401
            
            now = time.time()
            
            with _rate_limit_lock:
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


def check_cooldown(user_id: str, cooldown_seconds: float) -> tuple:
    """Check if user is on cooldown (thread-safe)"""
    now = time.time()
    
    with _cooldown_lock:
        if user_id in cooldown_storage:
            last_cast = cooldown_storage[user_id]
            time_elapsed = now - last_cast
            
            if time_elapsed < cooldown_seconds:
                remaining = cooldown_seconds - time_elapsed
                return False, remaining
    
    return True, 0


def set_cooldown(user_id: str):
    """Set cooldown for user (thread-safe)"""
    with _cooldown_lock:
        cooldown_storage[user_id] = time.time()


def cooldown_required(cooldown_seconds):
    """Decorator to enforce cooldown between casts (thread-safe)"""
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
            
            # Set cooldown BEFORE executing (prevents race condition)
            set_cooldown(user_id)
            
            # Execute function
            result = f(*args, **kwargs)
            
            # If the cast failed, we could optionally reset cooldown
            # but keeping it prevents rapid retry attacks
            
            return result
        
        return decorated_function
    return decorator
