# position_controller_futures.py
import time
import asyncio
import logging
import math
from risk_manager import RiskState

class PositionControllerFutures:
    """
    合约版本的仓位控制策略。
    基于每日更新的52日高低点，高频检查仓位并执行调整。
    适配合约交易的仓位管理和风险控制。
    """
    def __init__(self, trader_instance):
        """
        初始化合约仓位控制器。

        Args:
            trader_instance: 主 GridTrader 类的实例，用于访问交易所客户端、
                             获取账户信息、执行订单和日志记录。
        """
        self.trader = trader_instance
        self.config = trader_instance.config
        self.logger = logging.getLogger(self.__class__.__name__)

        # 合约策略参数
        self.s1_lookback = 52
        self.s1_sell_target_pct = 0.50  # 目标仓位比例（做多）
        self.s1_buy_target_pct = 0.70   # 目标仓位比例（做多）
        self.leverage = 10  # 默认杠杆倍数

        # S1 状态变量
        self.s1_daily_high = None
        self.s1_daily_low = None
        self.s1_last_data_update_ts = 0
        self.daily_update_interval = 23.9 * 60 * 60

        self.logger.info(f"合约仓位控制器初始化完成。回看期={self.s1_lookback}天, 卖出目标={self.s1_sell_target_pct*100}%, 买入目标={self.s1_buy_target_pct*100}%, 杠杆={self.leverage}x")

    async def _fetch_and_calculate_s1_levels(self):
        """获取日线数据并计算52日高低点"""
        try:
            limit = self.s1_lookback + 2
            klines = await self.trader.exchange.fetch_ohlcv(
                self.trader.symbol, 
                timeframe='1d', 
                limit=limit
            )

            if not klines or len(klines) < self.s1_lookback + 1:
                self.logger.warning(f"S1: 日线数据不足 ({len(klines)}), 无法更新水平线。")
                return False

            relevant_klines = klines[-(self.s1_lookback + 1) : -1]

            if len(relevant_klines) < self.s1_lookback:
                 self.logger.warning(f"S1: 有效K线数据不足 ({len(relevant_klines)}) 回看期 {self.s1_lookback}。")
                 return False

            self.s1_daily_high = max(float(k[2]) for k in relevant_klines)
            self.s1_daily_low = min(float(k[3]) for k in relevant_klines)
            self.s1_last_data_update_ts = time.time()
            self.logger.info(f"S1水平线更新: 高点={self.s1_daily_high:.4f}, 低点={self.s1_daily_low:.4f}")
            return True

        except Exception as e:
            self.logger.error(f"S1: 获取或计算日线水平线失败: {e}", exc_info=False)
            return False

    async def update_daily_s1_levels(self):
        """每日检查并更新一次S1所需的52日高低价"""
        now = time.time()
        if now - self.s1_last_data_update_ts >= self.daily_update_interval:
            self.logger.info("S1: 更新每日高低水平线...")
            await self._fetch_and_calculate_s1_levels()

    async def _get_current_position(self):
        """获取当前合约仓位信息"""
        try:
            positions = await self.trader.exchange.fetch_positions([self.trader.symbol])
            for position in positions:
                if position['symbol'] == self.trader.symbol:
                    return {
                        'size': float(position.get('size', 0)),  # 仓位大小
                        'side': position.get('side'),  # 'long' 或 'short'
                        'contracts': float(position.get('contracts', 0)),  # 合约数量
                        'notional': float(position.get('notional', 0)),  # 名义价值
                        'percentage': float(position.get('percentage', 0)),  # 仓位占用保证金比例
                        'unrealized_pnl': float(position.get('unrealizedPnl', 0)),  # 未实现盈亏
                        'entry_price': float(position.get('entryPrice', 0))  # 开仓均价
                    }
            return {
                'size': 0, 'side': None, 'contracts': 0, 'notional': 0, 
                'percentage': 0, 'unrealized_pnl': 0, 'entry_price': 0
            }
        except Exception as e:
            self.logger.error(f"获取仓位信息失败: {e}")
            return {
                'size': 0, 'side': None, 'contracts': 0, 'notional': 0, 
                'percentage': 0, 'unrealized_pnl': 0, 'entry_price': 0
            }

    async def _calculate_target_position_size(self, target_pct, current_price):
        """计算目标仓位大小"""
        try:
            # 获取账户余额
            balance = await self.trader.exchange.fetch_balance()
            total_balance = float(balance.get('total', {}).get('USDT', 0))
            
            if total_balance <= 0:
                self.logger.warning("账户余额为0，无法计算目标仓位")
                return 0
            
            # 计算目标名义价值
            target_notional = total_balance * target_pct * self.leverage
            
            # 转换为合约数量
            target_contracts = target_notional / current_price
            
            # 调整精度
            if hasattr(self.trader, '_adjust_amount_precision'):
                target_contracts = self.trader._adjust_amount_precision(target_contracts)
            else:
                precision = 3
                factor = 10 ** precision
                target_contracts = math.floor(target_contracts * factor) / factor
            
            return target_contracts
            
        except Exception as e:
            self.logger.error(f"计算目标仓位大小失败: {e}")
            return 0

    async def _execute_futures_adjustment(self, side, contracts_amount):
        """执行合约仓位调整"""
        try:
            if contracts_amount <= 0:
                self.logger.warning(f"合约数量无效: {contracts_amount}")
                return False

            current_price = self.trader.current_price
            if not current_price or current_price <= 0:
                self.logger.error("当前价格无效，无法执行调整")
                return False

            # 检查最小订单限制
            min_notional = 10  # USDT
            min_contracts = 0.001
            
            if hasattr(self.trader, 'symbol_info') and self.trader.symbol_info:
                limits = self.trader.symbol_info.get('limits', {})
                min_notional = limits.get('cost', {}).get('min', min_notional)
                min_contracts = limits.get('amount', {}).get('min', min_contracts)

            if contracts_amount < min_contracts:
                self.logger.warning(f"合约数量 {contracts_amount:.8f} 低于最小限制 {min_contracts:.8f}")
                return False
                
            if contracts_amount * current_price < min_notional:
                self.logger.warning(f"订单价值 {contracts_amount * current_price:.2f} USDT 低于最小名义价值 {min_notional:.2f}")
                return False

            self.logger.info(f"S1: 执行合约调整 {side} {contracts_amount:.8f} 合约，市价约 {current_price}")

            # 创建合约市价单
            order = await self.trader.exchange.create_futures_order(
                symbol=self.trader.symbol,
                side=side.lower(),
                amount=contracts_amount,
                order_type='market'
            )

            self.logger.info(f"S1: 合约调整订单成功。订单ID: {order.get('id', 'N/A')}")
            
            # 记录交易
            if hasattr(self.trader, 'order_tracker'):
                trade_info = {
                    'timestamp': time.time(),
                    'strategy': 'S1_Futures',
                    'side': side,
                    'price': float(order.get('average', current_price)),
                    'amount': float(order.get('filled', contracts_amount)),
                    'order_id': order.get('id')
                }
                self.trader.order_tracker.add_trade(trade_info)
                self.logger.info("S1: 合约交易已记录")

            return True

        except Exception as e:
            self.logger.error(f"S1: 执行合约调整失败 ({side} {contracts_amount:.8f}): {e}", exc_info=True)
            return False

    async def check_and_execute(self, risk_state: RiskState = RiskState.ALLOW_ALL):
        """检查S1合约仓位控制条件并执行调仓"""
        # 确保有当天的S1边界值
        if self.s1_daily_high is None or self.s1_daily_low is None:
            self.logger.debug("S1: 每日高低水平线尚未可用")
            return

        try:
            current_price = self.trader.current_price
            if not current_price or current_price <= 0:
                self.logger.warning("S1: 从trader获取的当前价格无效")
                return

            # 获取当前仓位
            current_position = await self._get_current_position()
            current_contracts = current_position['contracts']
            current_side = current_position['side']
            
            # 获取账户余额用于计算仓位比例
            balance = await self.trader.exchange.fetch_balance()
            total_balance = float(balance.get('total', {}).get('USDT', 0))
            
            if total_balance <= 0:
                self.logger.warning("S1: 账户余额无效")
                return

            # 计算当前仓位比例（基于名义价值）
            current_notional = abs(current_contracts) * current_price
            current_position_pct = current_notional / (total_balance * self.leverage)

            self.logger.debug(f"S1: 当前仓位 {current_contracts:.4f} 合约 ({current_side}), 比例 {current_position_pct:.2%}")

        except Exception as e:
            self.logger.error(f"S1: 获取当前状态失败: {e}")
            return

        # 判断S1条件
        s1_action = 'NONE'
        target_contracts = 0

        # 高点检查 - 减仓
        if current_price > self.s1_daily_high and current_position_pct > self.s1_sell_target_pct:
            target_contracts = await self._calculate_target_position_size(self.s1_sell_target_pct, current_price)
            
            if current_side == 'long' and current_contracts > target_contracts:
                # 减少多头仓位
                reduce_amount = current_contracts - target_contracts
                s1_action = 'SELL'
                self.logger.info(f"S1: 高点突破，需要减少多头仓位 {reduce_amount:.8f} 合约到目标 {self.s1_sell_target_pct*100:.0f}%")
                
                if risk_state != RiskState.ALLOW_BUY_ONLY:
                    await self._execute_futures_adjustment('SELL', reduce_amount)

        # 低点检查 - 加仓
        elif current_price < self.s1_daily_low and current_position_pct < self.s1_buy_target_pct:
            target_contracts = await self._calculate_target_position_size(self.s1_buy_target_pct, current_price)
            
            if current_side != 'short' and current_contracts < target_contracts:
                # 增加多头仓位
                increase_amount = target_contracts - current_contracts
                s1_action = 'BUY'
                self.logger.info(f"S1: 低点突破，需要增加多头仓位 {increase_amount:.8f} 合约到目标 {self.s1_buy_target_pct*100:.0f}%")
                
                if risk_state != RiskState.ALLOW_SELL_ONLY:
                    await self._execute_futures_adjustment('BUY', increase_amount)

        if s1_action != 'NONE' and risk_state not in [RiskState.ALLOW_BUY_ONLY, RiskState.ALLOW_SELL_ONLY]:
            self.logger.info(f"S1: {s1_action} 信号检测到但被风控阻止 (状态: {risk_state.name})")