# Strategy Pack v2: BUY / SELL / HOLD

## Objetivo

El strategy layer deja de inferir direccion a partir de un rango. Cada estrategia debe terminar explicitamente en una de tres acciones: `BUY`, `SELL` o `HOLD`.

La direccion proviene de evidencia que si es direccional —momentum, fuerza relativa, reversion contra un extremo o el ranker `weekly_opportunity_ridge`—. Los modelos Floor/Ceiling se usan principalmente para validar geometria de riesgo, definir invalidacion/objetivo y dimensionar la posicion.

`HOLD` es una salida de primera clase: si una oportunidad no supera los costos y guardrails, el sistema registra la razon y no genera una orden.

## Arquitectura por estrategia

Cada estrategia activa tiene un paquete propio. Ninguna implementacion direccional puede vivir en el registry, runner, allocator o en un archivo monolitico compartido.

```text
src/strategies/
├── common/
│   ├── __init__.py
│   └── mechanics.py
├── weekly_opportunity_ridge/
│   ├── __init__.py
│   └── strategy.py
├── breakout_protected_by_floor/
│   ├── __init__.py
│   └── strategy.py
├── mean_reversion_floor_w1/
│   ├── __init__.py
│   └── strategy.py
├── cross_horizon_asymmetry/
│   ├── __init__.py
│   └── strategy.py
├── registry.py
├── run_strategies.py
├── activation.py
├── portfolio_allocator.py
└── base.py
```

`registry.py` es la unica lista de implementaciones activas y solo enlaza `strategy_id -> generate_orders`. `common/` contiene exclusivamente mecanica compartida: costos, geometria Floor/Ceiling, liquidez, sizing, contexto M3 y construccion de HOLD.

Las rutas historicas `strategy_pack_v2.py`, `strategy_breakout_floor.py`, `strategy_mean_reversion.py` y `strategy_weekly_opportunity.py` se conservan solo como fachadas de compatibilidad sin logica. Un test estructural impide que vuelvan a acumular implementaciones.

## Contrato de costos

Existe un solo contrato de fricción para research, gates de estrategia y Strategy League. El round trip completo es **61 bps**:

- compra: 2 bps broker + 24 bps plataforma + 3 bps slippage = 29 bps;
- venta: 2 bps broker + 24 bps plataforma + 3 bps slippage + 3 bps sell fee = 32 bps;
- total compra + venta: **61 bps**.

La fórmula vive en `contracts.trading.round_trip_cost_bps_from_contract` y los adaptadores de estrategia reutilizan esa misma autoridad. No existe un gate optimista de 58 bps separado de una ejecución de 61 bps.

Floor/Ceiling sólo aportan geometría de payoff/riesgo. La alpha direccional debe superar el costo completo y el hurdle de cada estrategia antes de permitir `BUY` o `SELL`.

## Estrategias activas

### `weekly_opportunity_ridge`

El Ridge sigue siendo la fuente de alpha cross-sectional para Q1, pero desde Strategy League v9 su **target ya es neto de costos**: el retorno futuro se reduce por el round trip exacto de 61 bps antes de dividirse por el downside Q1. Movimientos cuya magnitud no cubre la fricción quedan en una zona muerta con target 0. El adaptador conoce esta semántica y no vuelve a descontar los costos una segunda vez. El top tail positivo puede producir `BUY`; el bottom tail negativo puede producir `SELL`; el resto produce `HOLD`.

La Strategy League continua long-only. Por ese motivo, dentro de la liga solo los `BUY` se convierten en targets; `SELL` y `HOLD` significan no abrir o dejar de mantener una posicion en el siguiente rebalanceo. El research runner conserva las tres acciones.

### `breakout_protected_by_floor`

Se conserva el identificador por compatibilidad, pero la logica se reconstruye como estrategia D1 simetrica. Momentum y fuerza relativa deciden la direccion. Floor/Ceiling validan el reward/risk y actuan como stop/target. Tendencia positiva puede dar `BUY`, tendencia negativa `SELL`; si la geometria no paga los costos, `HOLD`.

### `mean_reversion_floor_w1`

Ya no depende de `expected_return_w1`. La confirmación direccional es una **reversión 10d vs 20d** (`momentum_10 - momentum_20`): cerca del floor W1 puede comprar cuando el momentum corto ya mejora aunque el momentum de 20 días todavía sea negativo; cerca del ceiling aplica la señal simétrica. El anchor se amplía a 3%, el RR mínimo baja a 1.20 y el hurdle neto a 25 bps, pero la señal todavía debe cubrir los 61 bps, superar el múltiplo de costo, pasar liquidez y contexto M3.

### `cross_horizon_asymmetry`

Challenger diagnóstico. Combina la geometría D1/W1/Q1 con pesos 20%/30%/50% y exige confirmación de momentum/fuerza relativa. Su evidencia OOS existente es débil incluso antes de costos, por lo que **no se retunea contra ese mismo OOS**. Permanece como miembro observable de Strategy League, pero está cuarentenado del Capital Allocation Challenger (`capital_allocator_enabled: false`, source weight 0) hasta acumular evidencia nueva.

## Estrategias retiradas del registry activo

- `ai_only`
- `model_only`
- `consensus`

Sus implementaciones activas fueron retiradas del source root. La historia permanece en Git, pero ninguna de las tres se registra ni participa en research, paper o live. Dependian de señales direccionales que el contrato actual de Floor ya no considera evidencia suficiente.

## M3 como contexto

M3 no impone direccion por si mismo. Su timing solo afecta una decision cuando `floor_week_m3_confidence` supera el threshold configurado. Un floor M3 cercano y confiable puede reducir o bloquear un `BUY`; puede tambien elevar modestamente el contexto de un `SELL`. Si el timing no es confiable, no se usa como veto.

## Sizing y seguridad

El numero de acciones se limita por:

1. presupuesto de riesgo como porcentaje de NAV;
2. distancia entre precio y stop;
3. friccion round-trip estimada;
4. notional maximo por estrategia.

PAPER y LIVE permanecen desactivados. Ningún cambio activa ejecución real ni promoción automática. Los cambios de target Weekly y semántica de Mean Reversion abren una época limpia, `strategy_league_v9_net_target_reversal_10k`; la evidencia de v8 se conserva pero no se concatena con v9.
