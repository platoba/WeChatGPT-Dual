"""
函数调用注册表 - 支持OpenAI/Claude工具调用的函数注册与调度

Features:
- Register custom functions with JSON Schema parameter definitions
- Auto-generate OpenAI function calling schema
- Auto-generate Claude tool_use schema
- Function dispatch with parameter validation
- Async function support
- Function categories and permissions
- Rate limiting per function
- Execution logging and metrics
- Built-in functions (time, weather, calculator, search)
"""

import json
import time
import logging
import inspect
import asyncio
from typing import Dict, List, Optional, Any, Callable, Union
from dataclasses import dataclass, field, asdict
from enum import Enum
from functools import wraps

logger = logging.getLogger(__name__)


class ParamType(str, Enum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    ARRAY = "array"
    OBJECT = "object"


@dataclass
class FunctionParam:
    name: str
    param_type: str = "string"
    description: str = ""
    required: bool = True
    default: Any = None
    enum: Optional[List[str]] = None
    items_type: Optional[str] = None  # for arrays

    def to_json_schema(self) -> Dict[str, Any]:
        schema: Dict[str, Any] = {"type": self.param_type}
        if self.description:
            schema["description"] = self.description
        if self.enum:
            schema["enum"] = self.enum
        if self.param_type == "array" and self.items_type:
            schema["items"] = {"type": self.items_type}
        if self.default is not None:
            schema["default"] = self.default
        return schema


@dataclass
class FunctionDef:
    name: str
    description: str
    handler: Callable
    params: List[FunctionParam] = field(default_factory=list)
    category: str = "general"
    permissions: List[str] = field(default_factory=lambda: ["all"])
    rate_limit: int = 0  # 0 = unlimited, N = max calls per minute
    enabled: bool = True
    is_async: bool = False
    call_count: int = 0
    total_duration: float = 0.0
    last_called: float = 0.0
    _call_timestamps: List[float] = field(default_factory=list)

    def to_openai_schema(self) -> Dict[str, Any]:
        """Generate OpenAI function calling schema."""
        properties = {}
        required = []
        for p in self.params:
            properties[p.name] = p.to_json_schema()
            if p.required:
                required.append(p.name)

        schema: Dict[str, Any] = {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                },
            },
        }
        if required:
            schema["function"]["parameters"]["required"] = required
        return schema

    def to_claude_schema(self) -> Dict[str, Any]:
        """Generate Claude tool_use schema."""
        properties = {}
        required = []
        for p in self.params:
            properties[p.name] = p.to_json_schema()
            if p.required:
                required.append(p.name)

        schema: Dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": properties,
            },
        }
        if required:
            schema["input_schema"]["required"] = required
        return schema


class FunctionCallError(Exception):
    pass


class RateLimitError(FunctionCallError):
    pass


class PermissionError(FunctionCallError):
    pass


class ValidationError(FunctionCallError):
    pass


class FunctionRegistry:
    """Registry for AI function calling / tool use."""

    def __init__(self):
        self._functions: Dict[str, FunctionDef] = {}
        self._execution_log: List[Dict[str, Any]] = []
        self._max_log_size = 1000
        self._register_builtins()

    def register(
        self,
        name: str,
        handler: Callable,
        description: str = "",
        params: Optional[List[FunctionParam]] = None,
        category: str = "general",
        permissions: Optional[List[str]] = None,
        rate_limit: int = 0,
    ) -> FunctionDef:
        """Register a function for AI tool use."""
        is_async = inspect.iscoroutinefunction(handler)
        func_def = FunctionDef(
            name=name,
            description=description or handler.__doc__ or f"Function {name}",
            handler=handler,
            params=params or [],
            category=category,
            permissions=permissions or ["all"],
            rate_limit=rate_limit,
            is_async=is_async,
        )
        self._functions[name] = func_def
        logger.info(f"Registered function: {name} (category={category}, async={is_async})")
        return func_def

    def register_decorator(
        self,
        name: Optional[str] = None,
        description: str = "",
        params: Optional[List[FunctionParam]] = None,
        category: str = "general",
        permissions: Optional[List[str]] = None,
        rate_limit: int = 0,
    ):
        """Decorator to register a function."""
        def decorator(fn: Callable):
            fn_name = name or fn.__name__
            self.register(
                fn_name, fn, description=description, params=params,
                category=category, permissions=permissions, rate_limit=rate_limit,
            )
            return fn
        return decorator

    def unregister(self, name: str) -> bool:
        """Unregister a function."""
        if name in self._functions:
            del self._functions[name]
            return True
        return False

    def get(self, name: str) -> Optional[FunctionDef]:
        """Get function definition."""
        return self._functions.get(name)

    def list_functions(
        self,
        category: Optional[str] = None,
        enabled_only: bool = True,
    ) -> List[FunctionDef]:
        """List registered functions."""
        funcs = list(self._functions.values())
        if category:
            funcs = [f for f in funcs if f.category == category]
        if enabled_only:
            funcs = [f for f in funcs if f.enabled]
        return funcs

    def list_categories(self) -> List[str]:
        """List all function categories."""
        return sorted(set(f.category for f in self._functions.values()))

    def get_openai_tools(
        self,
        category: Optional[str] = None,
        user_permissions: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Get OpenAI function calling tool definitions."""
        funcs = self.list_functions(category=category)
        if user_permissions:
            funcs = [
                f for f in funcs
                if "all" in f.permissions or any(p in f.permissions for p in user_permissions)
            ]
        return [f.to_openai_schema() for f in funcs]

    def get_claude_tools(
        self,
        category: Optional[str] = None,
        user_permissions: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Get Claude tool_use definitions."""
        funcs = self.list_functions(category=category)
        if user_permissions:
            funcs = [
                f for f in funcs
                if "all" in f.permissions or any(p in f.permissions for p in user_permissions)
            ]
        return [f.to_claude_schema() for f in funcs]

    def call(
        self,
        name: str,
        arguments: Dict[str, Any],
        user_id: str = "",
        user_permissions: Optional[List[str]] = None,
    ) -> Any:
        """Call a registered function synchronously."""
        func_def = self._functions.get(name)
        if not func_def:
            raise FunctionCallError(f"Function '{name}' not found")

        if not func_def.enabled:
            raise FunctionCallError(f"Function '{name}' is disabled")

        # Permission check
        if user_permissions and "all" not in func_def.permissions:
            if not any(p in func_def.permissions for p in user_permissions):
                raise PermissionError(f"No permission to call '{name}'")

        # Rate limit check
        self._check_rate_limit(func_def)

        # Validate required parameters
        self._validate_params(func_def, arguments)

        # Execute
        start = time.time()
        try:
            if func_def.is_async:
                # Run async in event loop
                loop = asyncio.new_event_loop()
                try:
                    result = loop.run_until_complete(func_def.handler(**arguments))
                finally:
                    loop.close()
            else:
                result = func_def.handler(**arguments)

            duration = time.time() - start
            func_def.call_count += 1
            func_def.total_duration += duration
            func_def.last_called = time.time()
            func_def._call_timestamps.append(time.time())

            self._log_execution(name, arguments, result, duration, user_id, success=True)
            return result

        except Exception as e:
            duration = time.time() - start
            self._log_execution(name, arguments, str(e), duration, user_id, success=False)
            raise FunctionCallError(f"Error calling '{name}': {e}") from e

    async def call_async(
        self,
        name: str,
        arguments: Dict[str, Any],
        user_id: str = "",
        user_permissions: Optional[List[str]] = None,
    ) -> Any:
        """Call a registered function asynchronously."""
        func_def = self._functions.get(name)
        if not func_def:
            raise FunctionCallError(f"Function '{name}' not found")

        if not func_def.enabled:
            raise FunctionCallError(f"Function '{name}' is disabled")

        if user_permissions and "all" not in func_def.permissions:
            if not any(p in func_def.permissions for p in user_permissions):
                raise PermissionError(f"No permission to call '{name}'")

        self._check_rate_limit(func_def)
        self._validate_params(func_def, arguments)

        start = time.time()
        try:
            if func_def.is_async:
                result = await func_def.handler(**arguments)
            else:
                result = func_def.handler(**arguments)

            duration = time.time() - start
            func_def.call_count += 1
            func_def.total_duration += duration
            func_def.last_called = time.time()
            func_def._call_timestamps.append(time.time())

            self._log_execution(name, arguments, result, duration, user_id, success=True)
            return result

        except Exception as e:
            duration = time.time() - start
            self._log_execution(name, arguments, str(e), duration, user_id, success=False)
            raise FunctionCallError(f"Error calling '{name}': {e}") from e

    def dispatch_openai_tool_call(
        self,
        tool_call: Dict[str, Any],
        user_id: str = "",
    ) -> Dict[str, Any]:
        """Dispatch an OpenAI tool call response."""
        func_name = tool_call.get("function", {}).get("name", "")
        args_str = tool_call.get("function", {}).get("arguments", "{}")
        call_id = tool_call.get("id", "")

        try:
            arguments = json.loads(args_str) if isinstance(args_str, str) else args_str
        except json.JSONDecodeError:
            return {
                "tool_call_id": call_id,
                "role": "tool",
                "content": json.dumps({"error": "Invalid JSON arguments"}),
            }

        try:
            result = self.call(func_name, arguments, user_id=user_id)
            return {
                "tool_call_id": call_id,
                "role": "tool",
                "content": json.dumps(result) if not isinstance(result, str) else result,
            }
        except FunctionCallError as e:
            return {
                "tool_call_id": call_id,
                "role": "tool",
                "content": json.dumps({"error": str(e)}),
            }

    def dispatch_claude_tool_use(
        self,
        tool_use: Dict[str, Any],
        user_id: str = "",
    ) -> Dict[str, Any]:
        """Dispatch a Claude tool_use block."""
        func_name = tool_use.get("name", "")
        arguments = tool_use.get("input", {})
        tool_use_id = tool_use.get("id", "")

        try:
            result = self.call(func_name, arguments, user_id=user_id)
            content = json.dumps(result) if not isinstance(result, str) else result
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": content,
            }
        except FunctionCallError as e:
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": json.dumps({"error": str(e)}),
                "is_error": True,
            }

    def get_metrics(self) -> Dict[str, Any]:
        """Get function call metrics."""
        metrics = {}
        for name, func_def in self._functions.items():
            avg_duration = (
                func_def.total_duration / func_def.call_count
                if func_def.call_count > 0
                else 0
            )
            metrics[name] = {
                "call_count": func_def.call_count,
                "total_duration": round(func_def.total_duration, 3),
                "avg_duration": round(avg_duration, 3),
                "last_called": func_def.last_called,
                "category": func_def.category,
                "enabled": func_def.enabled,
            }
        return metrics

    def get_execution_log(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent execution log."""
        return self._execution_log[-limit:]

    def enable(self, name: str) -> bool:
        func = self._functions.get(name)
        if func:
            func.enabled = True
            return True
        return False

    def disable(self, name: str) -> bool:
        func = self._functions.get(name)
        if func:
            func.enabled = False
            return True
        return False

    def _check_rate_limit(self, func_def: FunctionDef):
        if func_def.rate_limit <= 0:
            return
        now = time.time()
        # Clean old timestamps
        func_def._call_timestamps = [
            t for t in func_def._call_timestamps if now - t < 60
        ]
        if len(func_def._call_timestamps) >= func_def.rate_limit:
            raise RateLimitError(
                f"Rate limit exceeded for '{func_def.name}': "
                f"{func_def.rate_limit}/min"
            )

    def _validate_params(self, func_def: FunctionDef, arguments: Dict[str, Any]):
        required = [p.name for p in func_def.params if p.required]
        for param_name in required:
            if param_name not in arguments:
                raise ValidationError(
                    f"Missing required parameter '{param_name}' for '{func_def.name}'"
                )

        param_types = {p.name: p.param_type for p in func_def.params}
        type_map = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
            "array": list,
            "object": dict,
        }
        for name, value in arguments.items():
            if name in param_types:
                expected = type_map.get(param_types[name])
                if expected and not isinstance(value, expected):
                    raise ValidationError(
                        f"Parameter '{name}' expected {param_types[name]}, "
                        f"got {type(value).__name__}"
                    )

        # Check enums
        for p in func_def.params:
            if p.enum and p.name in arguments:
                if arguments[p.name] not in p.enum:
                    raise ValidationError(
                        f"Parameter '{p.name}' must be one of {p.enum}"
                    )

    def _log_execution(
        self,
        name: str,
        arguments: Dict[str, Any],
        result: Any,
        duration: float,
        user_id: str,
        success: bool,
    ):
        entry = {
            "function": name,
            "arguments": arguments,
            "result": str(result)[:500] if result else None,
            "duration": round(duration, 3),
            "user_id": user_id,
            "success": success,
            "timestamp": time.time(),
        }
        self._execution_log.append(entry)
        if len(self._execution_log) > self._max_log_size:
            self._execution_log = self._execution_log[-self._max_log_size:]

    def _register_builtins(self):
        """Register built-in utility functions."""

        def get_current_time(timezone: str = "UTC") -> str:
            """Get current time in specified timezone."""
            from datetime import datetime, timezone as tz
            if timezone == "UTC":
                return datetime.now(tz.utc).isoformat()
            return datetime.now().isoformat()

        self.register(
            "get_current_time",
            get_current_time,
            description="Get the current date and time",
            params=[
                FunctionParam(
                    name="timezone",
                    param_type="string",
                    description="Timezone (default: UTC)",
                    required=False,
                    default="UTC",
                ),
            ],
            category="utility",
        )

        def calculator(expression: str) -> str:
            """Evaluate a mathematical expression safely."""
            allowed = set("0123456789+-*/().% ")
            if not all(c in allowed for c in expression):
                return "Error: Invalid characters in expression"
            try:
                result = eval(expression, {"__builtins__": {}}, {})
                return str(result)
            except Exception as e:
                return f"Error: {e}"

        self.register(
            "calculator",
            calculator,
            description="Evaluate a mathematical expression",
            params=[
                FunctionParam(
                    name="expression",
                    param_type="string",
                    description="Math expression to evaluate (e.g., '2+3*4')",
                ),
            ],
            category="utility",
        )

        def string_tools(action: str, text: str) -> str:
            """String manipulation tools."""
            actions = {
                "upper": text.upper,
                "lower": text.lower,
                "reverse": lambda: text[::-1],
                "length": lambda: str(len(text)),
                "words": lambda: str(len(text.split())),
                "strip": text.strip,
                "title": text.title,
            }
            fn = actions.get(action)
            if fn:
                return fn()
            return f"Unknown action: {action}. Available: {list(actions.keys())}"

        self.register(
            "string_tools",
            string_tools,
            description="String manipulation tools (upper, lower, reverse, length, etc.)",
            params=[
                FunctionParam(
                    name="action",
                    param_type="string",
                    description="Action to perform",
                    enum=["upper", "lower", "reverse", "length", "words", "strip", "title"],
                ),
                FunctionParam(
                    name="text",
                    param_type="string",
                    description="Text to process",
                ),
            ],
            category="utility",
        )

        def json_format(data: str, indent: int = 2) -> str:
            """Format JSON string."""
            try:
                parsed = json.loads(data)
                return json.dumps(parsed, indent=indent, ensure_ascii=False)
            except json.JSONDecodeError as e:
                return f"Invalid JSON: {e}"

        self.register(
            "json_format",
            json_format,
            description="Format and validate JSON data",
            params=[
                FunctionParam(name="data", description="JSON string to format"),
                FunctionParam(
                    name="indent", param_type="integer",
                    description="Indent spaces", required=False, default=2,
                ),
            ],
            category="utility",
        )
