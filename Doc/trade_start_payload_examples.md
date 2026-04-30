# Trade Start Payload Examples

Use these payloads with `POST /signals/tradingview/webhook` to test technical trade-start conditions without `pinebitz_tsc`.

Base fields used in all examples:

```json
{
  "plan_name": "BTC USDT DCA Demo",
  "owner_key": "smoke-owner",
  "symbol": "BTCUSDT",
  "side": "buy",
  "timeframe": "1h",
  "price": 65000,
  "volume": 1
}
```

## RSI (example: compare `lt`, value `30`)

```json
{
  "plan_name": "BTC USDT DCA Demo",
  "owner_key": "smoke-owner",
  "symbol": "BTCUSDT",
  "side": "buy",
  "timeframe": "1h",
  "price": 65000,
  "volume": 1,
  "rsi": 25.4
}
```

## MACD (example: crossing up + less than 0)

```json
{
  "plan_name": "BTC USDT DCA Demo",
  "owner_key": "smoke-owner",
  "symbol": "BTCUSDT",
  "side": "buy",
  "timeframe": "1h",
  "price": 65000,
  "volume": 1,
  "macd": -0.12,
  "signal": -0.18
}
```

## Stochastic (example: K < 20 and K crossing up D)

```json
{
  "plan_name": "BTC USDT DCA Demo",
  "owner_key": "smoke-owner",
  "symbol": "BTCUSDT",
  "side": "buy",
  "timeframe": "1h",
  "price": 65000,
  "volume": 1,
  "stoch_k": 18.2,
  "stoch_d": 15.0
}
```

## MA (example: condition `price_above`)

```json
{
  "plan_name": "BTC USDT DCA Demo",
  "owner_key": "smoke-owner",
  "symbol": "BTCUSDT",
  "side": "buy",
  "timeframe": "1h",
  "price": 65000,
  "volume": 1,
  "ma": 64000
}
```

## Nested indicator payload shape

You can also send values in nested form:

```json
{
  "plan_name": "BTC USDT DCA Demo",
  "owner_key": "smoke-owner",
  "symbol": "BTCUSDT",
  "side": "buy",
  "timeframe": "1h",
  "price": 65000,
  "volume": 1,
  "indicators": {
    "rsi": { "value": 28.7 },
    "macd": { "macd": -0.11, "signal": -0.16 },
    "stochastic": { "k": 17.5, "d": 14.1 },
    "ma": { "value": 64500 }
  }
}
```

## Quick send command (PowerShell)

```powershell
$body = @'
{
  "plan_name":"BTC USDT DCA Demo",
  "owner_key":"smoke-owner",
  "symbol":"BTCUSDT",
  "side":"buy",
  "timeframe":"1h",
  "price":65000,
  "volume":1,
  "rsi":25.4
}
'@

Invoke-RestMethod -Method POST `
  -Uri "http://127.0.0.1:8000/signals/tradingview/webhook" `
  -Headers @{ "X-Owner-Key" = "smoke-owner" } `
  -ContentType "application/json" `
  -Body $body
```
