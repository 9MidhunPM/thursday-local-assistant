from __future__ import annotations

from typing import Any

from assistant.tools.base import BaseTool, ToolMetadata


class UnitConvertTool(BaseTool):
    """Tool to convert between different units of measurement."""
    metadata = ToolMetadata(
        name="convert",
        description="Convert between different units (length, weight, temperature, volume, time, speed). Examples: '10 km to miles', '25 celsius to fahrenheit', '5 kg to pounds'",
        parameters={
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Conversion expression (e.g., '10 km to miles', '25 c to f')"
                }
            },
            "required": ["expression"],
        },
    )

    def __init__(self):
        # Conversion factors to base units
        self.length_units = {
            'mm': 0.001, 'cm': 0.01, 'm': 1.0, 'km': 1000.0,
            'in': 0.0254, 'inches': 0.0254, 'in': 0.0254,
            'ft': 0.3048, 'feet': 0.3048, 'foot': 0.3048,
            'yd': 0.9144, 'yards': 0.9144, 'yard': 0.9144,
            'mi': 1609.34, 'miles': 1609.34, 'mile': 1609.34
        }
        
        self.weight_units = {
            'mg': 0.000001, 'g': 0.001, 'kg': 1.0,
            'oz': 0.0283495, 'ounces': 0.0283495, 'ounce': 0.0283495,
            'lb': 0.453592, 'pounds': 0.453592, 'pound': 0.453592,
            'stone': 6.35029
        }
        
        self.volume_units = {
            'ml': 0.001, 'l': 1.0, 'liters': 1.0, 'liter': 1.0,
            'tsp': 0.00492892, 'teaspoon': 0.00492892, 'teaspoons': 0.00492892,
            'tbsp': 0.0147868, 'tablespoon': 0.0147868, 'tablespoons': 0.0147868,
            'cup': 0.236588, 'cups': 0.236588,
            'pt': 0.473176, 'pint': 0.473176, 'pints': 0.473176,
            'qt': 0.946353, 'quart': 0.946353, 'quarts': 0.946353,
            'gal': 3.78541, 'gallon': 3.78541, 'gallons': 3.78541
        }
        
        self.time_units = {
            's': 1.0, 'seconds': 1.0, 'second': 1.0,
            'min': 60.0, 'minutes': 60.0, 'minute': 60.0,
            'h': 3600.0, 'hours': 3600.0, 'hour': 3600.0,
            'day': 86400.0, 'days': 86400.0,
            'week': 604800.0, 'weeks': 604800.0,
            'year': 31536000.0, 'years': 31536000.0  # non-leap year
        }
        
        self.speed_units = {
            'm/s': 1.0, 'meters/second': 1.0,
            'km/h': 0.277778, 'kilometers/hour': 0.277778, 'kph': 0.277778,
            'mph': 0.44704, 'miles/hour': 0.44704,
            'knot': 0.514444, 'knots': 0.514444,
            'ft/s': 0.3048, 'feet/second': 0.3048
        }

    def _convert_temperature(self, value: float, from_unit: str, to_unit: str) -> float:
        """Convert temperature between Celsius, Fahrenheit, and Kelvin."""
        # Convert to Celsius first
        if from_unit in ['c', 'celsius']:
            celsius = value
        elif from_unit in ['f', 'fahrenheit']:
            celsius = (value - 32) * 5/9
        elif from_unit in ['k', 'kelvin']:
            celsius = value - 273.15
        else:
            raise ValueError(f"Unsupported temperature unit: {from_unit}")
        
        # Convert from Celsius to target unit
        if to_unit in ['c', 'celsius']:
            return celsius
        elif to_unit in ['f', 'fahrenheit']:
            return celsius * 9/5 + 32
        elif to_unit in ['k', 'kelvin']:
            return celsius + 273.15
        else:
            raise ValueError(f"Unsupported temperature unit: {to_unit}")

    def execute(self, arguments: dict[str, Any], context: Any) -> dict[str, Any]:
        expression = arguments.get("expression", "").strip()
        if not expression:
            return {"success": False, "error": "Expression is required"}

        try:
            # Parse the expression (format: "value unit1 to unit2")
            expression = expression.lower().strip()
            
            # Handle special case for temperature abbreviations
            if ' to ' in expression:
                left, right = expression.split(' to ', 1)
                left = left.strip()
                right = right.strip()
                
                # Parse left side: should be "value unit"
                parts = left.split()
                if len(parts) < 2:
                    return {"success": False, "error": "Invalid format. Use: 'value unit1 to unit2'"}
                
                try:
                    value = float(parts[0])
                    from_unit = ' '.join(parts[1:])
                except ValueError:
                    return {"success": False, "error": "Invalid value"}
                
                to_unit = right
                
                # Handle temperature conversions separately
                temp_units = {'c', 'celsius', 'f', 'fahrenheit', 'k', 'kelvin'}
                if from_unit in temp_units and to_unit in temp_units:
                    result = self._convert_temperature(value, from_unit, to_unit)
                    return {
                        "success": True,
                        "expression": expression,
                        "result": result,
                        "from_unit": from_unit,
                        "to_unit": to_unit
                    }
                
                # Handle other conversions
                all_units = {
                    **self.length_units,
                    **self.weight_units,
                    **self.volume_units,
                    **self.time_units,
                    **self.speed_units
                }
                
                if from_unit not in all_units:
                    return {"success": False, "error": f"Unknown unit: {from_unit}"}
                if to_unit not in all_units:
                    return {"success": False, "error": f"Unknown unit: {to_unit}"}
                
                # Convert to base unit, then to target unit
                value_in_base = value * all_units[from_unit]
                result = value_in_base / all_units[to_unit]
                
                return {
                    "success": True,
                    "expression": expression,
                    "result": result,
                    "from_unit": from_unit,
                    "to_unit": to_unit
                }
            else:
                return {"success": False, "error": "Invalid format. Use: 'value unit1 to unit2'"}
                
        except Exception as e:
            return {"success": False, "error": f"Error in conversion: {str(e)}"}


def get_tools(config: Any | None = None) -> list[BaseTool]:
    return [UnitConvertTool()]