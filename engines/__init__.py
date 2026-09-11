from . import execution, risk, strategy, traders, validation

ENGINES = {
    'execution': execution,
    'risk': risk,
    'strategy': strategy,
    'traders': traders,
    'validation': validation,
}
