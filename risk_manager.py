import logging
import enum
from config import settings


class RiskState(enum.Enum):
    """定义风险状态的枚举"""
    ALLOW_ALL = 0        # 允许所有操作
    ALLOW_SELL_ONLY = 1  # 只允许卖出 (仓位已满)
    ALLOW_BUY_ONLY = 2   # 只允许买入 (底仓保护)


class AdvancedRiskManager:
    def __init__(self, trader):
        self.trader = trader
        self.logger = logging.getLogger(self.__class__.__name__)
        # 初始化日志状态标记
        self._min_limit_warning_logged = False
        self._max_limit_warning_logged = False
    
    async def check_position_limits(self, spot_balance, funding_balance) -> RiskState:
        """检查仓位限制并返回相应的风险状态，同时控制日志频率"""
        try:
            position_ratio = await self._get_position_ratio(spot_balance, funding_balance) # 传递参数

            # 保存上次的仓位比例
            if not hasattr(self, 'last_position_ratio'):
                self.last_position_ratio = position_ratio

            # 只在仓位比例变化超过0.1%时打印日志
            if abs(position_ratio - self.last_position_ratio) > 0.001:
                self.logger.info(
                    f"风控检查 | "
                    f"当前仓位比例: {position_ratio:.2%} | "
                    f"最大允许比例: {settings.MAX_POSITION_RATIO:.2%} | "
                    f"最小底仓比例: {settings.MIN_POSITION_RATIO:.2%}"
                )
                self.last_position_ratio = position_ratio

            # 检查仓位是否超限 (> 90%)
            if position_ratio > settings.MAX_POSITION_RATIO:
                # 只有在没打印过日志时才打印
                if not self._max_limit_warning_logged:
                    self.logger.warning(f"仓位超限 ({position_ratio:.2%})，暂停新的买入操作。")
                    self._max_limit_warning_logged = True  # 标记为已打印

                # 无论是否打印日志，都要重置另一个标记
                self._min_limit_warning_logged = False
                return RiskState.ALLOW_SELL_ONLY

            # 检查是否触发底仓保护 (< 10%)
            elif position_ratio < settings.MIN_POSITION_RATIO:
                # 只有在没打印过日志时才打印
                if not self._min_limit_warning_logged:
                    self.logger.warning(f"底仓保护触发 ({position_ratio:.2%})，暂停新的卖出操作。")
                    self._min_limit_warning_logged = True  # 标记为已打印

                # 无论是否打印日志，都要重置另一个标记
                self._max_limit_warning_logged = False
                return RiskState.ALLOW_BUY_ONLY

            # 如果仓位在安全范围内 (10% ~ 90%)
            else:
                # 如果之前有警告，现在恢复正常了，就打印一条恢复信息
                if self._min_limit_warning_logged or self._max_limit_warning_logged:
                    self.logger.info(f"仓位已恢复至正常范围 ({position_ratio:.2%})。")

                # 将所有日志标记重置为False
                self._min_limit_warning_logged = False
                self._max_limit_warning_logged = False
                return RiskState.ALLOW_ALL

        except Exception as e:
            self.logger.error(f"风控检查失败: {str(e)}")
            # 在异常情况下也重置标记，以防状态锁死
            self._min_limit_warning_logged = False
            self._max_limit_warning_logged = False
            return RiskState.ALLOW_ALL  # 出现异常时，默认为允许所有操作以避免卡死

    # 保留原方法以保持向后兼容性
    async def multi_layer_check(self):
        """向后兼容的方法，将新的风控状态转换为布尔值"""
        # 获取账户快照
        spot_balance = await self.trader.exchange.fetch_balance()
        funding_balance = await self.trader.exchange.fetch_funding_balance()

        risk_state = await self.check_position_limits(spot_balance, funding_balance)
        return risk_state != RiskState.ALLOW_ALL

    async def _get_position_value(self, futures_balance, funding_balance=None):
        """获取合约仓位价值"""
        try:
            # 获取当前仓位信息
            positions = await self.trader.exchange.fetch_positions([self.trader.symbol])
            position_value = 0
            
            for position in positions:
                if position['symbol'] == self.trader.symbol:
                    # 使用名义价值作为仓位价值
                    position_value = abs(float(position.get('notional', 0)))
                    break
            
            return position_value
        except Exception as e:
            self.trader.logger.error(f"获取合约仓位价值失败: {e}")
            return 0

    async def _get_position_ratio(self, futures_balance, funding_balance=None):
        """获取当前合约仓位占总资产比例"""
        try:
            position_value = await self._get_position_value(futures_balance, funding_balance)
            
            # 获取USDT余额（合约保证金）
            usdt_balance = float(futures_balance.get('free', {}).get('USDT', 0) or 0)
            usdt_used = float(futures_balance.get('used', {}).get('USDT', 0) or 0)
            
            # 获取未实现盈亏
            positions = await self.trader.exchange.fetch_positions([self.trader.symbol])
            unrealized_pnl = 0
            for position in positions:
                if position['symbol'] == self.trader.symbol:
                    unrealized_pnl = float(position.get('unrealizedPnl', 0))
                    break
            
            # 总资产 = 可用余额 + 已用保证金 + 未实现盈亏
            total_assets = usdt_balance + usdt_used + unrealized_pnl
            
            if total_assets <= 0:
                return 0

            # 仓位比例 = 仓位价值 / (总资产 * 杠杆)
            # 这样计算可以反映实际的风险敞口
            ratio = position_value / (total_assets * self.trader.leverage)
            
            self.logger.debug(
                f"合约仓位计算 | "
                f"仓位价值: {position_value:.2f} USDT | "
                f"总资产: {total_assets:.2f} USDT | "
                f"杠杆: {self.trader.leverage}x | "
                f"仓位比例: {ratio:.2%}"
            )
            return ratio
        except Exception as e:
            self.logger.error(f"计算合约仓位比例失败: {str(e)}")
            return 0

    async def check_market_sentiment(self):
        """检查市场情绪指标"""
        try:
            fear_greed = await self._get_fear_greed_index()
            if fear_greed < 20:  # 极度恐惧
                # 注意：这里修改的是全局设置，会影响所有交易对
                settings.RISK_FACTOR *= 0.5  # 降低风险系数
            elif fear_greed > 80:  # 极度贪婪
                settings.RISK_FACTOR *= 1.2  # 提高风险系数
        except Exception as e:
            self.logger.error(f"获取市场情绪失败: {str(e)}") 