from __future__ import annotations

import re
import math
import operator
from typing import Any, Callable

from assistant.tools.base import BaseTool, ToolMetadata


class CalculatorTool(BaseTool):
    """Tool to evaluate mathematical expressions."""
    metadata = ToolMetadata(
        name="calculate",
        description="Evaluate mathematical expressions. Supports basic arithmetic (+,-,*,/,^,**), functions (sin, cos, tan, log, ln, sqrt, abs), and constants (pi, e).",
        parameters={
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Mathematical expression to evaluate (e.g., '2 + 3 * 4', 'sqrt(16)', 'sin(pi/2)')"
                }
            },
            "required": ["expression"],
        },
    )

    def __init__(self):
        # Define safe operators
        self.operators: dict[str, Callable[[float, float], float]] = {
            '+': operator.add,
            '-': operator.sub,
            '*': operator.mul,
            '/': operator.truediv,
            '^': operator.pow,
            '**': operator.pow,
        }
        
        # Define functions
        self.functions: dict[str, Callable[[float], float]] = {
            'sin': math.sin,
            'cos': math.cos,
            'tan': math.tan,
            'asin': math.asin,
            'acos': math.acos,
            'atan': math.atan,
            'sinh': math.sinh,
            'cosh': math.cosh,
            'tanh': math.tanh,
            'log': math.log10,  # log base 10
            'ln': math.log,     # natural log
            'sqrt': math.sqrt,
            'abs': abs,
            'ceil': math.ceil,
            'floor': math.floor,
            'round': round,
        }
        
        # Define constants
        self.constants: dict[str, float] = {
            'pi': math.pi,
            'e': math.e,
        }

    def _tokenize(self, expression: str) -> list[str]:
        """Convert expression string into tokens."""
        # Remove spaces
        expression = expression.replace(' ', '')
        
        # Tokenize using regex
        # Matches numbers (including decimals and scientific notation), 
        # operators, functions, parentheses, and constants
        token_pattern = r'(\d+\.?\d*(?:[eE][+-]?\d+)?|[a-zA-Z_][a-zA-Z0-9_]*|\+|\-|\*|\/|\^|\*\*|\(|\))'
        tokens = re.findall(token_pattern, expression)
        
        return tokens

    def _shunting_yard(self, tokens: list[str]) -> list[str]:
        """Convert infix notation to postfix notation using Shunting-yard algorithm."""
        output = []
        operators = []
        
        # Precedence levels (higher number = higher precedence)
        precedence = {
            '+': 1, '-': 1,
            '*': 2, '/': 2,
            '^': 3, '**': 3,
        }
        
        for token in tokens:
            # If token is a number or constant, add to output
            if self._is_number(token) or token in self.constants:
                output.append(token)
            # If token is a function, push to operator stack
            elif token in self.functions:
                operators.append(token)
            # If token is an operator
            elif token in self.operators:
                while (operators and 
                       operators[-1] in self.operators and
                       ((precedence.get(operators[-1], 0) > precedence.get(token, 0)) or
                        (precedence.get(operators[-1], 0) == precedence.get(token, 0) and token in ['+', '-', '*', '/']))):
                    output.append(operators.pop())
                operators.append(token)
            # If token is left parenthesis, push to stack
            elif token == '(':
                operators.append(token)
            # If token is right parenthesis, pop operators until left parenthesis
            elif token == ')':
                while operators and operators[-1] != '(':
                    output.append(operators.pop())
                if operators and operators[-1] == '(':
                    operators.pop()  # Discard the '('
                else:
                    raise ValueError("Mismatched parentheses")
                # If there's a function on top of the stack, add it to output
                if operators and operators[-1] in self.functions:
                    output.append(operators.pop())
            else:
                raise ValueError(f"Unknown token: {token}")
        
        # Pop any remaining operators to output
        while operators:
            if operators[-1] in ['(', ')']:
                raise ValueError("Mismatched parentheses")
            output.append(operators.pop())
        
        return output

    def _is_number(self, token: str) -> bool:
        """Check if token represents a number."""
        try:
            float(token)
            return True
        except ValueError:
            return False

    def _evaluate_postfix(self, tokens: list[str]) -> float:
        """Evaluate postfix expression."""
        stack = []
        
        for token in tokens:
            if self._is_number(token):
                stack.append(float(token))
            elif token in self.constants:
                stack.append(self.constants[token])
            elif token in self.functions:
                if len(stack) < 1:
                    raise ValueError("Insufficient arguments for function")
                operand = stack.pop()
                result = self.functions[token](operand)
                stack.append(result)
            elif token in self.operators:
                if len(stack) < 2:
                    raise ValueError("Insufficient arguments for operator")
                b = stack.pop()
                a = stack.pop()
                result = self.operators[token](a, b)
                stack.append(result)
            else:
                raise ValueError(f"Unknown token in postfix: {token}")
        
        if len(stack) != 1:
            raise ValueError("Invalid expression")
        
        return stack[0]

    def execute(self, arguments: dict[str, Any], context: Any) -> dict[str, Any]:
        expression = arguments.get("expression", "").strip()
        if not expression:
            return {"success": False, "error": "Expression is required"}
        
        try:
            # Handle special cases first
            expression = expression.lower()
            
            # Tokenize
            tokens = self._tokenize(expression)
            
            # Convert to postfix
            postfix = self._shunting_yard(tokens)
            
            # Evaluate
            result = self._evaluate_postfix(postfix)
            
            return {
                "success": True,
                "expression": expression,
                "result": result
            }
        except Exception as e:
            return {"success": False, "error": f"Error calculating expression: {str(e)}"}


def get_tools(config: Any | None = None) -> list[BaseTool]:
    return [CalculatorTool()]