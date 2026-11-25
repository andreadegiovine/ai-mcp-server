"""Strumento: calcolatrice sicura (senza eval)."""

import ast
import math
import operator
import re

from nucleo.server import UserError, tool


def _pow_sicura(a, b):
    if isinstance(b, (int, float)) and abs(b) > 10000:
        raise ValueError("esponente troppo grande")
    if isinstance(a, int) and isinstance(b, int) and b > 0 and a not in (0, 1, -1) and b * math.log2(abs(a)) > 100000:
        raise ValueError("risultato troppo grande")
    return operator.pow(a, b)


def _mul_sicura(a, b):
    if isinstance(a, int) and isinstance(b, int) and a and b and (abs(a).bit_length() + abs(b).bit_length()) > 200000:
        raise ValueError("risultato troppo grande")
    return operator.mul(a, b)


def _fattoriale(n):
    if not float(n).is_integer() or n < 0 or n > 1000:
        raise ValueError("fattoriale ammesso solo per interi 0..1000")
    return math.factorial(int(n))


_OPERATORI = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: _mul_sicura,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: _pow_sicura,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}
_FUNZIONI = {
    "sqrt": math.sqrt,
    "radice": math.sqrt,
    "cbrt": lambda x: math.copysign(abs(x) ** (1 / 3), x),
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "atan2": math.atan2,
    "sinh": math.sinh,
    "cosh": math.cosh,
    "tanh": math.tanh,
    "log": math.log,
    "ln": math.log,
    "log10": math.log10,
    "log2": math.log2,
    "exp": math.exp,
    "abs": abs,
    "round": round,
    "floor": math.floor,
    "ceil": math.ceil,
    "trunc": math.trunc,
    "factorial": _fattoriale,
    "fattoriale": _fattoriale,
    "gcd": math.gcd,
    "lcm": math.lcm,
    "hypot": math.hypot,
    "degrees": math.degrees,
    "radians": math.radians,
    "min": min,
    "max": max,
    "sum": lambda *a: sum(a),
    "media": lambda *a: sum(a) / len(a),
    "comb": math.comb,
    "perm": math.perm,
}
_COSTANTI = {"pi": math.pi, "pigreco": math.pi, "e": math.e, "tau": math.tau, "inf": math.inf}


def _valuta(nodo):
    if isinstance(nodo, ast.Constant) and isinstance(nodo.value, (int, float)) and not isinstance(nodo.value, bool):
        return nodo.value
    if isinstance(nodo, ast.BinOp) and type(nodo.op) in _OPERATORI:
        return _OPERATORI[type(nodo.op)](_valuta(nodo.left), _valuta(nodo.right))
    if isinstance(nodo, ast.UnaryOp) and type(nodo.op) in _OPERATORI:
        return _OPERATORI[type(nodo.op)](_valuta(nodo.operand))
    if isinstance(nodo, ast.Name) and nodo.id in _COSTANTI:
        return _COSTANTI[nodo.id]
    if (
        isinstance(nodo, ast.Call)
        and isinstance(nodo.func, ast.Name)
        and nodo.func.id in _FUNZIONI
        and not nodo.keywords
        and len(nodo.args) <= 50
    ):
        return _FUNZIONI[nodo.func.id](*[_valuta(a) for a in nodo.args])
    raise ValueError(f"elemento non consentito: {ast.dump(nodo)[:60]}")


def calcola_espressione(espressione: str):
    expr = espressione.strip()
    if len(expr) > 500:
        raise ValueError("espressione troppo lunga")
    expr = expr.replace("×", "*").replace("÷", "/").replace("^", "**").replace("√", "sqrt").replace("π", "pi")
    # Percentuali: "200 * 15%" -> "200 * (15/100)" (il % seguito da cifra/nome resta modulo)
    expr = re.sub(r"(\d+(?:\.\d+)?)\s*%(?!\s*[\d(a-zA-Z])", r"(\1/100)", expr)
    return _valuta(ast.parse(expr, mode="eval").body)


@tool(
    "calcola",
    title="Calcolatrice",
    description=(
        "Esegue calcoli matematici esatti da un'espressione testuale. Supporta + - * / // % ** (o ^), "
        "parentesi, percentuali (es. '250*15%'), costanti pi ed e e funzioni: sqrt, cbrt, sin, cos, tan, "
        "asin, acos, atan, log(x[,base]), ln, log10, log2, exp, abs, round(x,n), floor, ceil, factorial, "
        "gcd, lcm, hypot, degrees, radians, min, max, sum, media, comb, perm. Gli angoli sono in radianti. "
        "Usalo SEMPRE quando serve un risultato numerico preciso."
    ),
    read_only=True,
)
async def calcola(espressione: str) -> str:
    try:
        r = calcola_espressione(espressione)
    except ZeroDivisionError:
        raise UserError("Divisione per zero.")
    except (ValueError, TypeError, SyntaxError, OverflowError) as e:
        raise UserError(f"Espressione non valida: {e}")
    if isinstance(r, float):
        if r.is_integer() and abs(r) < 1e15:
            testo = str(int(r))
        else:
            testo = format(r, ".12g")
    else:
        try:
            testo = str(r)
        except ValueError:
            testo = f"(numero con circa {int(r.bit_length() * 0.30103)} cifre)"
    return f"{espressione} = {testo}"
