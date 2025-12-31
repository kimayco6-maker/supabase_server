"""
Fishing Game Logic
Server-side game mechanics with RPC calls to Supabase
"""
import random
from dataclasses import dataclass
from typing import Optional, List, Dict

@dataclass
class CatchResult:
    """Result of a fishing cast"""
    success: bool
    message: str
    fish: Optional[Dict] = None
    weight: Optional[float] = None
    points: Optional[int] = None
    is_personal_best: bool = False

class FishingGame:
    """Main game logic class"""
    
    def __init__(self, supabase_client):
        """Initialize game with Supabase client"""
        self.supabase = supabase_client
        self.fish_cache = []
        self._load_fish_species()
    
    def _load_fish_species(self):
        """Load all fish species from database and cache them"""
        try:
            response = self.supabase.table('fish_species').select('*').execute()
            self.fish_cache = response.data
            print(f"Loaded {len(self.fish_cache)} fish species")
        except Exception as e:
            print(f"Error loading fish species: {e}")
            self.fish_cache = []
    
    def _get_weighted_random_fish(self) -> Optional[Dict]:
        """
        Select a random fish based on rarity weights
        Rarity distribution:
        - common: 50%
        - uncommon: 25%
        - rare: 15%
        - epic: 7%
        - legendary: 3%
        """
        if not self.fish_cache:
            return None
        
        # Define rarity weights
        rarity_weights = {
            'common': 50,
            'uncommon': 25,
            'rare': 15,
            'epic': 7,
            'legendary': 3
        }
        
        # Group fish by rarity
        fish_by_rarity = {}
        for fish in self.fish_cache:
            rarity = fish['rarity']
            if rarity not in fish_by_rarity:
                fish_by_rarity[rarity] = []
            fish_by_rarity[rarity].append(fish)
        
        # Weighted random selection of rarity
        rarities = list(rarity_weights.keys())
        weights = [rarity_weights[r] for r in rarities]
        selected_rarity = random.choices(rarities, weights=weights, k=1)[0]
        
        # Random fish from selected rarity
        if selected_rarity in fish_by_rarity:
            return random.choice(fish_by_rarity[selected_rarity])
        
        return None
    
    def _generate_fish_weight(self, fish: Dict) -> float:
        """Generate random weight within fish species range"""
        min_weight = float(fish['min_weight'])
        max_weight = float(fish['max_weight'])
        
        # Use exponential distribution to make smaller catches more common
        # but still allow for trophy catches
        weight_range = max_weight - min_weight
        random_factor = random.expovariate(1.5)  # Favor lower values
        random_factor = min(random_factor, 3.0)  # Cap at 3.0
        weight = min_weight + (weight_range * (random_factor / 3.0))
        
        # Ensure within bounds
        weight = max(min_weight, min(weight, max_weight))
        
        return round(weight, 2)
    
    def _check_personal_best(self, player_id: str, fish_species_id: int, weight: float) -> bool:
        """Check if this catch is a personal best for this fish species"""
        try:
            response = self.supabase.table('catches')\
                .select('weight')\
                .eq('player_id', player_id)\
                .eq('fish_species_id', fish_species_id)\
                .order('weight', desc=True)\
                .limit(1)\
                .execute()
            
            if not response.data:
                return True  # First catch of this species
            
            previous_best = float(response.data[0]['weight'])
            return weight > previous_best
            
        except Exception as e:
            print(f"Error checking personal best: {e}")
            return False
    
    def save_catch(self, player_id: str, fish: Dict, weight: float, is_personal_best: bool) -> Dict:
        """
        Save catch to database using RPC function (bypasses RLS)
        Returns the catch details from the database
        """
        try:
            # Call the record_catch RPC function
            response = self.supabase.rpc(
                'record_catch',
                {
                    'p_player_id': player_id,
                    'p_fish_species_id': fish['id'],
                    'p_weight': weight,
                    'p_is_personal_best': is_personal_best
                }
            ).execute()
            
            if response.data and len(response.data) > 0:
                catch_data = response.data[0]
                return {
                    'id': catch_data['catch_id'],
                    'fish_name': catch_data['fish_name'],
                    'fish_rarity': catch_data['fish_rarity'],
                    'weight': float(catch_data['weight']),
                    'points': catch_data['points']
                }
            else:
                raise Exception("No data returned from record_catch function")
                
        except Exception as e:
            print(f"Error saving catch: {e}")
            raise
    
    def cast_line(self, player_id: str) -> CatchResult:
        """
        Main game action: Cast fishing line
        - Randomly selects a fish based on rarity
        - Generates random weight
        - Saves to database
        - Returns catch details
        """
        try:
            # Select random fish
            fish = self._get_weighted_random_fish()
            if not fish:
                return CatchResult(
                    success=False,
                    message="No fish available in the database"
                )
            
            # Generate weight
            weight = self._generate_fish_weight(fish)
            
            # Check if personal best
            is_personal_best = self._check_personal_best(player_id, fish['id'], weight)
            
            # Save catch to database via RPC
            catch_data = self.save_catch(player_id, fish, weight, is_personal_best)
            
            # Build success message
            message = f"You caught a {catch_data['fish_name']}!"
            if is_personal_best:
                message += " 🏆 Personal Best!"
            
            return CatchResult(
                success=True,
                message=message,
                fish=fish,
                weight=weight,
                points=catch_data['points'],
                is_personal_best=is_personal_best
            )
            
        except Exception as e:
            print(f"Error in cast_line: {e}")
            return CatchResult(
                success=False,
                message=f"Failed to cast line: {str(e)}"
            )
    
    def get_player_catches(self, player_id: str, limit: int = 50) -> List[Dict]:
        """Get player's recent catches"""
        try:
            response = self.supabase.table('catches')\
                .select('*, fish_species!inner(*)')\
                .eq('player_id', player_id)\
                .order('caught_at', desc=True)\
                .limit(limit)\
                .execute()
            
            return response.data
        except Exception as e:
            print(f"Error getting player catches: {e}")
            return []
    
    def get_player_stats(self, player_id: str) -> Optional[Dict]:
        """Get player statistics"""
        try:
            response = self.supabase.table('players')\
                .select('*')\
                .eq('id', player_id)\
                .single()\
                .execute()
            
            return response.data
        except Exception as e:
            print(f"Error getting player stats: {e}")
            return None
    
    def get_all_fish_species(self) -> List[Dict]:
        """Get all fish species for fish collection book"""
        return self.fish_cache
    
    def get_leaderboard_heaviest(self, limit: int = 100) -> List[Dict]:
        """Get leaderboard: heaviest single catch"""
        try:
            response = self.supabase.table('catches')\
                .select('*, players!inner(username), fish_species!inner(name, rarity)')\
                .order('weight', desc=True)\
                .limit(limit)\
                .execute()
            
            return response.data
        except Exception as e:
            print(f"Error getting heaviest leaderboard: {e}")
            return []
    
    def get_leaderboard_most_catches(self, limit: int = 100) -> List[Dict]:
        """Get leaderboard: most total catches"""
        try:
            response = self.supabase.table('players')\
                .select('*')\
                .order('total_catches', desc=True)\
                .limit(limit)\
                .execute()
            
            return response.data
        except Exception as e:
            print(f"Error getting most catches leaderboard: {e}")
            return []
    
    def get_leaderboard_rare_catches(self, limit: int = 100) -> List[Dict]:
        """Get leaderboard: most rare catches"""
        try:
            response = self.supabase.table('players')\
                .select('*')\
                .order('rare_catches', desc=True)\
                .limit(limit)\
                .execute()
            
            return response.data
        except Exception as e:
            print(f"Error getting rare catches leaderboard: {e}")
            return []

