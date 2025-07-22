# 🚀 合约交易系统快速启动指南

## 第一步：设置环境变量

```bash
# 设置API密钥
export BINANCE_API_KEY="你的API密钥"
export BINANCE_API_SECRET="你的API密钥密码"

# 设置代理（如果需要）
export HTTP_PROXY="http://127.0.0.1:10809"
export HTTPS_PROXY="http://127.0.0.1:10809"
```

## 第二步：测试网络连接

```bash
# 基础网络测试
python test_connection.py

# 如果网络测试通过，进行API测试
python simple_test.py
```

## 第三步：启动交易系统

```bash
# 如果所有测试都通过，启动合约交易
python main_futures.py
```

## 常见错误解决

### 1. 网络连接错误
```
ERROR: binance GET https://fapi.binance.com/fapi/v1/time
```
**解决方案**：
- 检查代理是否正常运行
- 确认代理地址：`127.0.0.1:10809`
- 运行 `python test_connection.py` 测试

### 2. API密钥错误
```
ERROR: Invalid API key
```
**解决方案**：
- 检查API密钥是否正确
- 确认API密钥有合约交易权限
- 检查IP白名单设置

### 3. 余额不足
```
ERROR: Insufficient balance
```
**解决方案**：
- 检查合约账户USDT余额
- 确保有足够保证金
- 降低杠杆倍数或交易金额

## 安全提醒

⚠️ **重要**：合约交易风险极高
- 建议先用小资金测试
- 设置合理的止损
- 不要使用过高杠杆
- 密切监控仓位和保证金

## 监控和日志

系统运行时会生成日志文件：
- 文件名：`futures_trading_YYYYMMDD.log`
- 实时监控：`tail -f futures_trading_*.log`

## 紧急停止

如需紧急停止交易：
- 按 `Ctrl+C` 优雅退出
- 系统会自动取消未成交订单
- 检查日志确认所有操作完成

---

**技术支持**：如遇问题，请查看日志文件并检查网络连接。