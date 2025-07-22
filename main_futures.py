#!/usr/bin/env python3
# main_futures.py - 合约交易启动脚本

import asyncio
import logging
import signal
import sys
from datetime import datetime

# 导入合约配置
from config_futures import futures_settings, FuturesConfig
from exchange_client import ExchangeClient
from trader import GridTrader

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(f'futures_trading_{datetime.now().strftime("%Y%m%d")}.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

class FuturesTrader:
    """合约交易主类"""
    
    def __init__(self):
        self.exchange = None
        self.traders = {}
        self.running = False
        
    async def initialize(self):
        """初始化交易所和交易器"""
        try:
            # 检查环境变量
            import os
            if not os.getenv('BINANCE_API_KEY') or not os.getenv('BINANCE_API_SECRET'):
                raise Exception("请设置 BINANCE_API_KEY 和 BINANCE_API_SECRET 环境变量")
            
            # 初始化交易所客户端
            logger.info("正在初始化合约交易所客户端...")
            self.exchange = ExchangeClient()
            
            # 先测试连接
            logger.info("测试网络连接...")
            await self.exchange.sync_time()
            logger.info("✓ 网络连接正常")
            
            # 加载市场数据
            logger.info("加载合约市场数据...")
            await self.exchange.load_markets()
            logger.info("✓ 市场数据加载完成")
            
            # 启动周期性时间同步
            await self.exchange.start_periodic_time_sync(interval_seconds=3600)
            
            logger.info("合约交易所客户端初始化完成")
            
            # 初始化交易器
            symbols = list(futures_settings.FUTURES_SYMBOLS.keys())
            logger.info(f"准备启动合约交易对: {symbols}")
            
            for symbol in symbols:
                try:
                    # 创建交易器实例
                    trader = GridTrader(self.exchange, FuturesConfig(), symbol)
                    
                    # 设置合约特有参数
                    symbol_config = futures_settings.FUTURES_SYMBOLS[symbol]
                    trader.leverage = symbol_config.get('leverage', futures_settings.DEFAULT_LEVERAGE)
                    trader.margin_mode = symbol_config.get('margin_mode', futures_settings.DEFAULT_MARGIN_MODE)
                    
                    # 初始化交易器
                    await trader.initialize()
                    
                    self.traders[symbol] = trader
                    logger.info(f"交易器 {symbol} 初始化完成，杠杆: {trader.leverage}x")
                    
                except Exception as e:
                    logger.error(f"初始化交易器 {symbol} 失败: {e}")
                    continue
            
            if not self.traders:
                raise Exception("没有成功初始化任何交易器")
                
            logger.info(f"成功初始化 {len(self.traders)} 个合约交易器")
            
        except Exception as e:
            logger.error(f"初始化失败: {e}")
            raise
    
    async def start_trading(self):
        """启动所有交易器"""
        if not self.traders:
            logger.error("没有可用的交易器")
            return
            
        self.running = True
        tasks = []
        
        try:
            # 为每个交易器创建任务
            for symbol, trader in self.traders.items():
                task = asyncio.create_task(
                    trader.main_loop(),
                    name=f"trader_{symbol.replace('/', '_')}"
                )
                tasks.append(task)
                logger.info(f"启动交易器任务: {symbol}")
            
            logger.info(f"所有 {len(tasks)} 个合约交易器已启动")
            
            # 等待所有任务完成
            await asyncio.gather(*tasks, return_exceptions=True)
            
        except Exception as e:
            logger.error(f"交易过程中发生错误: {e}")
        finally:
            # 取消所有任务
            for task in tasks:
                if not task.done():
                    task.cancel()
            
            # 等待任务清理
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
    
    async def shutdown(self):
        """安全关闭"""
        logger.info("开始关闭合约交易系统...")
        
        self.running = False
        
        # 关闭所有交易器
        for symbol, trader in self.traders.items():
            try:
                if hasattr(trader, 'emergency_stop'):
                    await trader.emergency_stop()
                logger.info(f"交易器 {symbol} 已关闭")
            except Exception as e:
                logger.error(f"关闭交易器 {symbol} 失败: {e}")
        
        # 关闭交易所连接
        if self.exchange:
            try:
                await self.exchange.stop_periodic_time_sync()
                await self.exchange.close()
                logger.info("交易所连接已关闭")
            except Exception as e:
                logger.error(f"关闭交易所连接失败: {e}")
        
        logger.info("合约交易系统已安全关闭")

# 全局变量
futures_trader = None

def signal_handler(signum, frame):
    """信号处理器"""
    logger.info(f"收到信号 {signum}，准备关闭...")
    if futures_trader:
        asyncio.create_task(futures_trader.shutdown())

async def main():
    """主函数"""
    global futures_trader
    
    # 注册信号处理器
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        logger.info("=" * 60)
        logger.info("启动币安合约网格交易系统")
        logger.info("=" * 60)
        
        # 创建合约交易器
        futures_trader = FuturesTrader()
        
        # 初始化
        await futures_trader.initialize()
        
        # 开始交易
        await futures_trader.start_trading()
        
    except KeyboardInterrupt:
        logger.info("收到键盘中断信号")
    except Exception as e:
        logger.error(f"程序异常退出: {e}")
    finally:
        if futures_trader:
            await futures_trader.shutdown()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("程序被用户中断")
    except Exception as e:
        logger.error(f"程序启动失败: {e}")
        sys.exit(1)