"""
Configuration management for the Fishing Game Backend
Using Supabase NEW API Key System (sb_publishable / sb_secret)
"""
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Config:
    """Application configuration"""
    
    # Supabase Configuration - NEW API KEY SYSTEM
    SUPABASE_URL = os.getenv('SUPABASE_URL')
    SUPABASE_SECRET_KEY = os.getenv('SUPABASE_SECRET_KEY')
    
    # Flask Configuration
    SECRET_KEY = os.getenv('FLASK_SECRET_KEY', 'dev-secret-key-change-in-production')
    DEBUG = os.getenv('FLASK_ENV') == 'development'
    
    # Game Configuration
    CAST_COOLDOWN_SECONDS = int(os.getenv('CAST_COOLDOWN_SECONDS', 2))
    MAX_REQUESTS_PER_MINUTE = int(os.getenv('MAX_REQUESTS_PER_MINUTE', 30))
    
    @staticmethod
    def validate():
        """Validate required configuration"""
        required = [
            'SUPABASE_URL',
            'SUPABASE_SECRET_KEY'
        ]
        
        missing = []
        for key in required:
            if not os.getenv(key):
                missing.append(key)
        
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
        
        return True

