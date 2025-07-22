import ccxt.async_support as ccxt
import os
import logging
from config import settings
from datetime import datetime
import time
import asyncio

class ExchangeClient:
    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        # API密钥验证已由Pydantic在settings实例化时自动完成
        
        # 获取代理配置
        proxy = os.getenv('HTTP_PROXY', 'http://127.0.0.1:10809')  # 使用你提供的代理地址
        
        # 先初始化交易所实例 - 合约模式
        self.exchange = ccxt.binance({
            'apiKey': settings.BINANCE_API_KEY,
            'secret': settings.BINANCE_API_SECRET,
            'enableRateLimit': True,
            'timeout': 30000,  # 减少超时时间到30秒
            'sandbox': False,  # 确保不是测试环境
            'options': {
                'defaultType': 'future',  # 改为合约模式
                'fetchMarkets': {
                    'spot': False,    # 禁用现货市场
                    'margin': False,  # 明确禁用杠杆
                    'swap': True,     # 启用永续合约
                    'future': True    # 启用期货合约
                },
                'fetchCurrencies': False,
                'recvWindow': 5000,  # 固定接收窗口
                'adjustForTimeDifference': True,  # 启用时间调整
                'warnOnFetchOpenOrdersWithoutSymbol': False,
                'createMarketBuyOrderRequiresPrice': False
            },
            'proxies': {
                'http': proxy,
                'https': proxy
            },
            'aiohttp_proxy': proxy,  # 添加aiohttp代理支持
            'verbose': settings.DEBUG_MODE if hasattr(settings, 'DEBUG_MODE') else False
        })
        if proxy:
            self.logger.info(f"使用代理: {proxy}")
        # 然后进行其他配置
        self.logger.setLevel(logging.INFO)
        self.logger.info("交易所客户端初始化完成")

        
        self.markets_loaded = False
        self.time_diff = 0
        self.balance_cache = {'timestamp': 0, 'data': None}
        self.funding_balance_cache = {'timestamp': 0, 'data': {}}
        self.cache_ttl = 30  # 缓存有效期（秒）

        # 为全局总资产计算添加缓存
        self.total_value_cache = {'timestamp': 0, 'data': 0.0}

        # 【新增】用于管理后台时间同步任务
        self.time_sync_task = None
    


    def _format_savings_amount(self, asset: str, amount: float) -> str:
        """根据配置格式化理财产品的操作金额"""
        # 从配置中获取该资产的理财精度，如果未指定，则使用默认精度
        precision = settings.SAVINGS_PRECISIONS.get(asset, settings.SAVINGS_PRECISIONS['DEFAULT'])

        # 使用 f-string 和获取到的精度来格式化
        return f"{float(amount):.{precision}f}"

    def _is_funding_balance_changed_significantly(
        self, old_balances: dict, new_balances: dict, relative_threshold: float = 0.001
    ) -> bool:
        """
        比较新旧理财余额，判断是否存在"重大变化"。
        通过比较相对变化百分比，智能忽略微小利息，且无需为新币种单独配置。

        Args:
            old_balances: 上一次缓存的余额字典。
            new_balances: 新获取的余额字典。
            relative_threshold: 相对变化阈值 (例如: 0.001 表示 0.1%)。

        Returns:
            True 如果任何资产的变化超过阈值，否则 False。
        """
        # 如果新旧余额完全相同，直接返回False，这是最高效的检查
        if new_balances == old_balances:
            return False

        # 获取所有涉及的资产（并集），以处理新增或移除的资产
        all_assets = set(old_balances.keys()) | set(new_balances.keys())

        for asset in all_assets:
            old_amount = old_balances.get(asset, 0.0)
            new_amount = new_balances.get(asset, 0.0)

            # 如果旧余额为0，任何新增都视为重大变化
            if old_amount == 0 and new_amount > 0:
                return True

            # 计算相对变化率
            # 使用 max(old_amount, 1e-9) 避免除以零的错误
            relative_change = abs(new_amount - old_amount) / max(old_amount, 1e-9)

            # 如果任何一个资产的相对变化超过了阈值，就认为发生了重大变化
            if relative_change > relative_threshold:
                return True

        # 如果所有资产的相对变化都未超过阈值，则认为没有重大变化
        return False

    async def load_markets(self):
        try:
            # 先同步时间
            await self.sync_time()
            
            # 添加重试机制
            max_retries = 3
            for i in range(max_retries):
                try:
                    await self.exchange.load_markets()
                    self.markets_loaded = True
                    self.logger.info(f"所有市场数据加载成功")
                    return True
                except Exception as e:
                    if i == max_retries - 1:
                        raise
                    self.logger.warning(f"加载市场数据失败，重试 {i+1}/{max_retries}")
                    await asyncio.sleep(2)
            
        except Exception as e:
            self.logger.error(f"加载市场数据失败: {str(e)}")
            self.markets_loaded = False
            raise

    async def fetch_ohlcv(self, symbol, timeframe='1h', limit=None):
        """获取K线数据"""
        try:
            params = {}
            if limit:
                params['limit'] = limit
            return await self.exchange.fetch_ohlcv(symbol, timeframe, params=params)
        except Exception as e:
            self.logger.error(f"获取K线数据失败: {str(e)}")
            raise
    
    async def fetch_ticker(self, symbol):
        self.logger.debug(f"获取行情数据 {symbol}...")
        start = datetime.now()
        try:
            # 使用市场ID进行请求
            market = self.exchange.market(symbol)
            ticker = await self.exchange.fetch_ticker(market['id'])
            latency = (datetime.now() - start).total_seconds()
            self.logger.debug(f"获取行情成功 | 延迟: {latency:.3f}s | 最新价: {ticker['last']}")
            return ticker
        except Exception as e:
            self.logger.error(f"获取行情失败: {str(e)}")
            self.logger.debug(f"请求参数: symbol={symbol}")
            raise

    async def fetch_funding_balance(self):
        """[已修复] 获取理财账户余额（支持分页）"""
        # 功能开关检查
        if not settings.ENABLE_SAVINGS_FUNCTION:
            # 如果理财功能关闭，直接返回空字典，并确保缓存也是空的
            self.funding_balance_cache = {'timestamp': 0, 'data': {}}
            return {}

        now = time.time()

        # 如果缓存有效，直接返回缓存数据
        if now - self.funding_balance_cache['timestamp'] < self.cache_ttl:
            return self.funding_balance_cache['data']

        all_balances = {}
        current_page = 1
        size_per_page = 100  # 使用API允许的最大值以减少请求次数

        try:
            while True:
                params = {'current': current_page, 'size': size_per_page}
                # 使用Simple Earn API，并传入分页参数
                result = await self.exchange.sapi_get_simple_earn_flexible_position(params)
                self.logger.debug(f"理财账户原始数据 (Page {current_page}): {result}")

                rows = result.get('rows', [])
                if not rows:
                    # 如果当前页没有数据，说明已经获取完毕
                    break

                for item in rows:
                    asset = item['asset']
                    amount = float(item.get('totalAmount', 0) or 0)
                    if asset in all_balances:
                        all_balances[asset] += amount
                    else:
                        all_balances[asset] = amount

                # 如果当前页返回的记录数小于每页大小，说明是最后一页
                if len(rows) < size_per_page:
                    break

                current_page += 1
                await asyncio.sleep(0.1)  # 避免请求过于频繁

            # 只在余额发生显著变化时打印日志（使用智能相对变化检测）
            old_balances = self.funding_balance_cache.get('data', {})
            if self._is_funding_balance_changed_significantly(old_balances, all_balances):
                self.logger.info(f"理财账户余额更新: {all_balances}")

            # 更新缓存
            self.funding_balance_cache = {
                'timestamp': now,
                'data': all_balances
            }

            return all_balances
        except Exception as e:
            self.logger.error(f"获取理财账户余额失败: {str(e)}")
            # 返回上一次的缓存（如果有）或空字典
            return self.funding_balance_cache.get('data', {})

    async def fetch_balance(self, params=None):
        """[已修复] 获取合约账户余额（含缓存机制）"""
        now = time.time()
        if now - self.balance_cache['timestamp'] < self.cache_ttl:
            return self.balance_cache['data']

        try:
            params = params or {}
            params['timestamp'] = int(time.time() * 1000) + self.time_diff
            balance = await self.exchange.fetch_balance(params)

            self.logger.debug(f"合约账户余额概要: {balance.get('total', {})}")
            self.balance_cache = {'timestamp': now, 'data': balance}
            return balance
        except Exception as e:
            self.logger.error(f"获取合约余额失败: {str(e)}")
            # 出错时不抛出异常，而是返回一个空的但结构完整的余额字典
            return {'free': {}, 'used': {}, 'total': {}}
    
    async def create_order(self, symbol, type, side, amount, price):
        try:
            # 在下单前重新同步时间
            await self.sync_time()
            # 添加时间戳到请求参数
            params = {
                'timestamp': int(time.time() * 1000 + self.time_diff),
                'recvWindow': 5000
            }
            return await self.exchange.create_order(symbol, type, side, amount, price, params)
        except Exception as e:
            self.logger.error(f"下单失败: {str(e)}")
            raise

    async def create_market_order(
        self,
        symbol: str,
        side: str,          # 只能是 'buy' 或 'sell'
        amount: float,
        params: dict | None = None
    ):
        """
        业务层需要的『市价单快捷封装』。
        实际还是调 ccxt 的 create_order，只是把 type 固定为 'market'。
        """
        # 确保有 params 字典
        params = params or {}

        # 下单前同步时间，避免 -1021 错误
        await self.sync_time()
        params.update({
            'timestamp': int(time.time() * 1000 + self.time_diff),
            'recvWindow': 5000
        })

        order = await self.exchange.create_order(
            symbol=symbol,
            type='market',
            side=side.lower(),   # ccxt 规范小写
            amount=amount,
            price=None,          # 市价单 price 必须是 None
            params=params
        )
        return order


    async def fetch_order(self, order_id, symbol, params=None):
        if params is None:
            params = {}
        params['timestamp'] = int(time.time() * 1000 + self.time_diff)
        params['recvWindow'] = 5000
        return await self.exchange.fetch_order(order_id, symbol, params)
    
    async def fetch_open_orders(self, symbol):
        """获取当前未成交订单"""
        return await self.exchange.fetch_open_orders(symbol)
    
    async def cancel_order(self, order_id, symbol, params=None):
        """取消指定订单"""
        if params is None:
            params = {}
        params['timestamp'] = int(time.time() * 1000 + self.time_diff)
        params['recvWindow'] = 5000
        return await self.exchange.cancel_order(order_id, symbol, params)
    
    async def close(self):
        """关闭交易所连接"""
        try:
            if self.exchange:
                await self.exchange.close()
                self.logger.info("交易所连接已安全关闭")
        except Exception as e:
            self.logger.error(f"关闭连接时发生错误: {str(e)}")

    async def sync_time(self):
        """同步交易所服务器时间"""
        try:
            server_time = await self.exchange.fetch_time()
            local_time = int(time.time() * 1000)
            # 【关键】更新 self.time_diff
            self.time_diff = server_time - local_time
            # 将日志级别从 INFO 改为 DEBUG，避免频繁刷屏
            self.logger.debug(f"时间同步完成 | 新时差: {self.time_diff}ms")
        except Exception as e:
            self.logger.error(f"周期性时间同步失败: {str(e)}")

    async def fetch_order_book(self, symbol, limit=5):
        """获取订单簿数据"""
        try:
            market = self.exchange.market(symbol)
            return await self.exchange.fetch_order_book(market['id'], limit=limit)
        except Exception as e:
            self.logger.error(f"获取订单簿失败: {str(e)}")
            raise

    async def get_flexible_product_id(self, asset):
        """获取指定资产的活期理财产品ID"""
        try:
            params = {
                'asset': asset,
                'timestamp': int(time.time() * 1000 + self.time_diff),
                'current': 1,  # 当前页
                'size': 100,   # 每页数量
            }
            result = await self.exchange.sapi_get_simple_earn_flexible_list(params)
            products = result.get('rows', [])
            
            # 查找对应资产的活期理财产品
            for product in products:
                if product['asset'] == asset and product['status'] == 'PURCHASING':
                    self.logger.info(f"找到{asset}活期理财产品: {product['productId']}")
                    return product['productId']
            
            raise ValueError(f"未找到{asset}的可用活期理财产品")
        except Exception as e:
            self.logger.error(f"获取活期理财产品失败: {str(e)}")
            raise

    async def transfer_to_spot(self, asset, amount):
        """从活期理财赎回到现货账户"""
        try:
            # 获取产品ID
            product_id = await self.get_flexible_product_id(asset)
            
            # 使用配置化的精度格式化金额
            formatted_amount = self._format_savings_amount(asset, amount)
            
            params = {
                'asset': asset,
                'amount': formatted_amount,
                'productId': product_id,
                'timestamp': int(time.time() * 1000 + self.time_diff),
                'redeemType': 'FAST'  # 快速赎回
            }
            self.logger.info(f"开始赎回: {formatted_amount} {asset} 到现货")
            result = await self.exchange.sapi_post_simple_earn_flexible_redeem(params)
            self.logger.info(f"划转成功: {result}")
            
            # 赎回后清除余额缓存，确保下次获取最新余额
            self.balance_cache = {'timestamp': 0, 'data': None}
            self.funding_balance_cache = {'timestamp': 0, 'data': {}}
            
            return result
        except Exception as e:
            self.logger.error(f"赎回失败: {str(e)}")
            raise

    async def transfer_to_savings(self, asset, amount):
        """从现货账户申购活期理财"""
        try:
            # 获取产品ID
            product_id = await self.get_flexible_product_id(asset)
            
            # 使用配置化的精度格式化金额
            formatted_amount = self._format_savings_amount(asset, amount)
            
            params = {
                'asset': asset,
                'amount': formatted_amount,
                'productId': product_id,
                'timestamp': int(time.time() * 1000 + self.time_diff)
            }
            self.logger.info(f"开始申购: {formatted_amount} {asset} 到活期理财")
            result = await self.exchange.sapi_post_simple_earn_flexible_subscribe(params)
            self.logger.info(f"划转成功: {result}")
            
            # 申购后清除余额缓存，确保下次获取最新余额
            self.balance_cache = {'timestamp': 0, 'data': None}
            self.funding_balance_cache = {'timestamp': 0, 'data': {}}
            
            return result
        except Exception as e:
            self.logger.error(f"申购失败: {str(e)}")
            raise

    async def fetch_my_trades(self, symbol, limit=10):
        """获取指定交易对的最近成交记录"""
        self.logger.debug(f"获取最近 {limit} 条成交记录 for {symbol}...")
        if not self.markets_loaded:
            await self.load_markets()
        try:
            # 确保使用市场ID
            market = self.exchange.market(symbol)
            trades = await self.exchange.fetch_my_trades(market['id'], limit=limit)
            self.logger.info(f"成功获取 {len(trades)} 条最近成交记录 for {symbol}")
            return trades
        except Exception as e:
            self.logger.error(f"获取成交记录失败 for {symbol}: {str(e)}")
            # 返回空列表或根据需要处理错误
            return []

    async def fetch_positions(self, symbols=None):
        """获取合约仓位信息"""
        try:
            positions = await self.exchange.fetch_positions(symbols)
            return positions
        except Exception as e:
            self.logger.error(f"获取仓位信息失败: {str(e)}")
            return []

    async def set_leverage(self, symbol, leverage):
        """设置合约杠杆倍数"""
        try:
            result = await self.exchange.set_leverage(leverage, symbol)
            self.logger.info(f"设置杠杆成功: {symbol} -> {leverage}x")
            return result
        except Exception as e:
            self.logger.error(f"设置杠杆失败: {str(e)}")
            raise

    async def set_margin_mode(self, symbol, margin_mode='isolated'):
        """设置保证金模式 (isolated/cross)"""
        try:
            result = await self.exchange.set_margin_mode(margin_mode, symbol)
            self.logger.info(f"设置保证金模式成功: {symbol} -> {margin_mode}")
            return result
        except Exception as e:
            self.logger.error(f"设置保证金模式失败: {str(e)}")
            raise

    async def create_futures_order(self, symbol, side, amount, price=None, order_type='market', 
                                 reduce_only=False, time_in_force='GTC'):
        """创建合约订单"""
        try:
            await self.sync_time()
            params = {
                'timestamp': int(time.time() * 1000 + self.time_diff),
                'recvWindow': 5000
            }
            
            if reduce_only:
                params['reduceOnly'] = True
            if time_in_force:
                params['timeInForce'] = time_in_force
                
            order = await self.exchange.create_order(
                symbol=symbol,
                type=order_type,
                side=side.lower(),
                amount=amount,
                price=price,
                params=params
            )
            return order
        except Exception as e:
            self.logger.error(f"创建合约订单失败: {str(e)}")
            raise

    async def calculate_total_account_value(self, quote_currency: str = 'USDT', min_value_threshold: float = 1.0) -> float:
        """
        【最终修复版】计算整个账户的总资产价值。
        此版本修复了因 fetch_balance() 返回理财凭证而导致的重复计算BUG。
        """
        now = time.time()
        if now - self.total_value_cache['timestamp'] < self.cache_ttl:
            return self.total_value_cache['data']

        try:
            # 1. 获取现货和理财账户的余额
            spot_balance = await self.fetch_balance()
            funding_balance = await self.fetch_funding_balance()

            # --- 核心修复逻辑开始 ---

            # 2. 创建一个干净的合并字典
            combined_balances = {}

            # 3. 首先，只处理真正的现货余额。
            # 我们遍历现货账户返回的所有资产，但【明确跳过】所有以 'LD' 开头的理财凭证。
            # 这确保了我们只累加纯粹的现货资产。
            if spot_balance and 'total' in spot_balance:
                for asset, amount in spot_balance['total'].items():
                    if float(amount) > 0 and not asset.startswith('LD'):
                        combined_balances[asset] = combined_balances.get(asset, 0.0) + float(amount)

            # 4. 然后，将专门获取的、干净的理财账户余额加进来。
            # 因为上一步已经排除了 'LD' 资产，这里的累加绝对不会重复。
            if funding_balance:
                for asset, amount in funding_balance.items():
                    if float(amount) > 0:
                        combined_balances[asset] = combined_balances.get(asset, 0.0) + float(amount)

            # --- 核心修复逻辑结束 ---

            total_value = 0.0

            # 5. 后续的计价逻辑保持不变，因为它现在处理的是一个干净、无重复的资产列表
            for asset, amount in combined_balances.items():
                if amount <= 0:
                    continue

                asset_value = 0.0

                # 注意：这里的 'LD' 处理逻辑依然需要保留，因为在某些极罕见情况下，
                # funding_balance 可能直接返回带 'LD' 的key。这是一种防御性编程。
                original_asset = asset
                if asset.startswith('LD'):
                    original_asset = asset[2:]

                if original_asset == quote_currency:
                    asset_value = amount
                else:
                    try:
                        symbol = f"{original_asset}/{quote_currency}"
                        ticker = await self.fetch_ticker(symbol)
                        if ticker and 'last' in ticker and ticker['last'] > 0:
                            asset_value = amount * ticker['last']
                        else:
                            continue
                    except Exception:
                        continue

                if asset_value >= min_value_threshold:
                    total_value += asset_value

            self.total_value_cache = {'timestamp': now, 'data': total_value}
            return total_value

        except Exception as e:
            self.logger.error(f"计算全账户总资产价值失败: {e}", exc_info=True)
            return self.total_value_cache.get('data', 0.0)

    async def start_periodic_time_sync(self, interval_seconds: int = 3600):
        """
        启动一个后台任务，周期性地同步交易所时间。

        Args:
            interval_seconds: 同步间隔，单位为秒。默认为 3600秒（1小时）。
        """
        if self.time_sync_task is not None:
            self.logger.warning("时间同步任务已经启动，无需重复启动。")
            return

        async def _time_sync_loop():
            self.logger.info(f"启动周期性时间同步任务，每 {interval_seconds} 秒执行一次。")
            while True:
                try:
                    await self.sync_time()
                    await asyncio.sleep(interval_seconds)
                except asyncio.CancelledError:
                    self.logger.info("时间同步任务被取消。")
                    break
                except Exception as e:
                    self.logger.error(f"时间同步循环发生错误: {e}，将在60秒后重试。")
                    await asyncio.sleep(60)

        # 创建并启动后台任务
        self.time_sync_task = asyncio.create_task(_time_sync_loop())

    async def stop_periodic_time_sync(self):
        """安全地停止周期性时间同步任务。"""
        if self.time_sync_task and not self.time_sync_task.done():
            self.time_sync_task.cancel()
            try:
                await self.time_sync_task
            except asyncio.CancelledError:
                pass  # 任务被取消是正常现象
            self.logger.info("周期性时间同步任务已停止。")
        self.time_sync_task = None