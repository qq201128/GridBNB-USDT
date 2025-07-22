# config_futures.py - 合约交易配置
try:
    from config import *
except ImportError:
    # 如果无法导入config，提供基本配置
    import os
    from pydantic import BaseSettings
    
    class Settings(BaseSettings):
        BINANCE_API_KEY: str = os.getenv('BINANCE_API_KEY', '')
        BINANCE_API_SECRET: str = os.getenv('BINANCE_API_SECRET', '')
        DEBUG_MODE: bool = False
        SAFETY_MARGIN: float = 0.95
    
    settings = Settings()
    
    class TradingConfig:
        pass

# 重写部分配置以适配合约交易
class FuturesConfig(TradingConfig):
    """合约交易配置"""
    
    # 合约特有参数
    DEFAULT_LEVERAGE = 10  # 默认杠杆倍数
    DEFAULT_MARGIN_MODE = 'isolated'  # 保证金模式
    
    # 风险控制参数调整（合约风险更高）
    MAX_POSITION_RATIO = 0.8  # 最大仓位比例80%
    MIN_POSITION_RATIO = 0.1  # 最小仓位比例10%
    
    # 最小交易金额调整
    MIN_TRADE_AMOUNT = 10  # USDT
    
    # 网格参数调整
    INITIAL_GRID = 2.0  # 初始网格大小2%
    
    # 动态调整参数
    DYNAMIC_INTERVAL_PARAMS = {
        'default_interval_hours': 2.0,  # 默认调整间隔2小时
        'volatility_to_interval_hours': [
            {'range': [0.0, 0.02], 'interval_hours': 4.0},   # 低波动率4小时
            {'range': [0.02, 0.05], 'interval_hours': 2.0},  # 中波动率2小时
            {'range': [0.05, 0.10], 'interval_hours': 1.0},  # 高波动率1小时
            {'range': [0.10, 1.0], 'interval_hours': 0.5}    # 极高波动率30分钟
        ]
    }

# 合约交易对配置
FUTURES_SYMBOLS = {
    'BTC/USDT': {
        'leverage': 10,
        'margin_mode': 'isolated',
        'initial_base_price': 0,  # 使用实时价格
        'initial_grid': 2.0
    },
    'ETH/USDT': {
        'leverage': 15,
        'margin_mode': 'isolated', 
        'initial_base_price': 0,
        'initial_grid': 2.5
    },
    'BNB/USDT': {
        'leverage': 10,
        'margin_mode': 'isolated',
        'initial_base_price': 0,
        'initial_grid': 3.0
    }
}

# 更新设置以支持合约
class FuturesSettings(Settings):
    """合约交易设置"""
    
    # 禁用理财功能（合约交易不需要）
    ENABLE_SAVINGS_FUNCTION: bool = False
    
    # 合约特有配置
    FUTURES_SYMBOLS: dict = FUTURES_SYMBOLS
    DEFAULT_LEVERAGE: int = 10
    DEFAULT_MARGIN_MODE: str = 'isolated'
    
    # 风险控制
    MAX_POSITION_RATIO: float = 0.8
    MIN_POSITION_RATIO: float = 0.1
    
    # 最小交易金额
    MIN_TRADE_AMOUNT: float = 10.0

# 创建合约设置实例
futures_settings = FuturesSettings()