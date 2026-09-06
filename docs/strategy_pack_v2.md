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

La plataforma cobra 0.24% por cada compra y 0.24% por cada venta. El strategy layer modela por lado:

- plataforma: 24 bps;
- comision broker existente: 2 bps;
- slippage: 3 bps.

Esto produce un costo base de 29 bps por lado y 58 bps para un round trip. Las estrategias comparan el upside/downside potencial contra ese costo antes de permitir `BUY` o `SELL`.

El sizing tambien incorpora la friccion round-trip junto con la distancia al stop. En `Strategy League` se conserva ademas el `sell_fee_bps` historico del simulador, por lo que su contabilidad puede ser algo mas conservadora que el gate base de 58 bps.

## Estrategias activas

### `weekly_opportunity_ridge`

El Ridge sigue siendo la fuente de alpha cross-sectional para Q1. El top tail positivo puede producir `BUY`; el bottom tail negativo puede producir `SELL`; el resto produce `HOLD`. Un score extremo no basta: Q1 floor/ceiling deben ofrecer reward/risk y edge neto suficientes despues de costos.

La Strategy League continua long-only. Por ese motivo, dentro de la liga solo los `BUY` se convierten en targets; `SELL` y `HOLD` significan no abrir o dejar de mantener una posicion en el siguiente rebalanceo. El research runner conserva las tres acciones.

### `breakout_protected_by_floor`

Se conserva el identificador por compatibilidad, pero la logica se reconstruye como estrategia D1 simetrica. Momentum y fuerza relativa deciden la direccion. Floor/Ceiling validan el reward/risk y actuan como stop/target. Tendencia positiva puede dar `BUY`, tendencia negativa `SELL`; si la geometria no paga los costos, `HOLD`.

### `mean_reversion_floor_w1`

Ya no depende de `expected_return_w1`. Cerca del floor W1, una estabilizacion de momentum puede producir `BUY`; cerca del ceiling W1, una perdida de momentum puede producir `SELL`. Si el precio no esta suficientemente cerca de un extremo, la respuesta es `HOLD`.

### `cross_horizon_asymmetry`

Nuevo challenger. Combina la geometria D1/W1/Q1 con pesos 20%/30%/50%. Solo toma una direccion si la asimetria entre upside y downside supera el threshold y momentum/fuerza relativa confirman el mismo sentido. Si los horizontes no ofrecen una asimetria clara neta de costos, devuelve `HOLD`.

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

PAPER y LIVE permanecen desactivados. Ningun cambio activa ejecucion real ni promocion automatica. Como este refactor es exclusivamente estructural, no reinicia `strategy_league_v4`: IDs, parametros, costos y comportamiento de las estrategias permanecen iguales.
