"""Tests for FunctionRegistry."""

import json
import pytest

from services.function_registry import (
    FunctionRegistry, FunctionParam, FunctionCallError, RateLimitError, ValidationError,
)


@pytest.fixture
def registry():
    return FunctionRegistry()


@pytest.fixture
def clean_registry():
    """Registry without builtins."""
    reg = FunctionRegistry()
    for name in list(reg._functions.keys()):
        reg.unregister(name)
    return reg


class TestRegistration:
    def test_register_function(self, clean_registry):
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        func = clean_registry.register(
            "greet", greet,
            description="Greet someone",
            params=[FunctionParam(name="name", description="Person's name")],
        )
        assert func.name == "greet"
        assert func.description == "Greet someone"
        assert len(func.params) == 1

    def test_register_with_decorator(self, clean_registry):
        @clean_registry.register_decorator(name="add", description="Add two numbers")
        def add(a: int, b: int) -> int:
            return a + b

        assert "add" in clean_registry._functions

    def test_register_async_function(self, clean_registry):
        async def async_fn(x: int) -> int:
            return x * 2

        func = clean_registry.register("async_fn", async_fn)
        assert func.is_async

    def test_unregister(self, clean_registry):
        clean_registry.register("tmp", lambda: None)
        assert clean_registry.unregister("tmp")
        assert clean_registry.get("tmp") is None

    def test_unregister_nonexistent(self, clean_registry):
        assert not clean_registry.unregister("nope")

    def test_get_function(self, registry):
        func = registry.get("calculator")
        assert func is not None
        assert func.name == "calculator"

    def test_get_nonexistent(self, registry):
        assert registry.get("nonexistent") is None


class TestListing:
    def test_list_all(self, registry):
        funcs = registry.list_functions()
        assert len(funcs) >= 3  # builtins

    def test_list_by_category(self, registry):
        funcs = registry.list_functions(category="utility")
        assert all(f.category == "utility" for f in funcs)

    def test_list_categories(self, registry):
        cats = registry.list_categories()
        assert "utility" in cats

    def test_list_enabled_only(self, clean_registry):
        clean_registry.register("fn1", lambda: 1)
        clean_registry.register("fn2", lambda: 2)
        clean_registry.disable("fn2")
        enabled = clean_registry.list_functions(enabled_only=True)
        assert len(enabled) == 1
        all_funcs = clean_registry.list_functions(enabled_only=False)
        assert len(all_funcs) == 2


class TestSchemaGeneration:
    def test_openai_schema(self, registry):
        tools = registry.get_openai_tools()
        assert len(tools) >= 1
        tool = tools[0]
        assert tool["type"] == "function"
        assert "function" in tool
        assert "name" in tool["function"]
        assert "parameters" in tool["function"]

    def test_claude_schema(self, registry):
        tools = registry.get_claude_tools()
        assert len(tools) >= 1
        tool = tools[0]
        assert "name" in tool
        assert "input_schema" in tool
        assert tool["input_schema"]["type"] == "object"

    def test_openai_schema_with_permissions(self, clean_registry):
        clean_registry.register(
            "admin_fn", lambda: "admin",
            permissions=["admin"],
        )
        clean_registry.register(
            "user_fn", lambda: "user",
            permissions=["all"],
        )
        tools = clean_registry.get_openai_tools(user_permissions=["user"])
        names = [t["function"]["name"] for t in tools]
        assert "user_fn" in names
        assert "admin_fn" not in names

    def test_claude_schema_with_category(self, registry):
        tools = registry.get_claude_tools(category="utility")
        assert all(
            registry.get(t["name"]).category == "utility" for t in tools
        )

    def test_param_schema_with_enum(self):
        param = FunctionParam(
            name="action",
            param_type="string",
            enum=["start", "stop"],
        )
        schema = param.to_json_schema()
        assert schema["enum"] == ["start", "stop"]

    def test_param_schema_with_array(self):
        param = FunctionParam(
            name="items",
            param_type="array",
            items_type="string",
        )
        schema = param.to_json_schema()
        assert schema["items"] == {"type": "string"}


class TestFunctionCalling:
    def test_call_builtin_calculator(self, registry):
        result = registry.call("calculator", {"expression": "2+3*4"})
        assert result == "14"

    def test_call_string_tools(self, registry):
        result = registry.call("string_tools", {"action": "upper", "text": "hello"})
        assert result == "HELLO"

    def test_call_custom_function(self, clean_registry):
        def multiply(a: int, b: int) -> int:
            return a * b

        clean_registry.register(
            "multiply", multiply,
            params=[
                FunctionParam(name="a", param_type="integer"),
                FunctionParam(name="b", param_type="integer"),
            ],
        )
        result = clean_registry.call("multiply", {"a": 3, "b": 7})
        assert result == 21

    def test_call_nonexistent(self, registry):
        with pytest.raises(FunctionCallError, match="not found"):
            registry.call("nonexistent", {})

    def test_call_disabled_function(self, registry):
        registry.disable("calculator")
        with pytest.raises(FunctionCallError, match="disabled"):
            registry.call("calculator", {"expression": "1+1"})
        registry.enable("calculator")

    def test_call_with_missing_required_param(self, clean_registry):
        clean_registry.register(
            "need_name", lambda name: name,
            params=[FunctionParam(name="name", required=True)],
        )
        with pytest.raises(ValidationError, match="Missing required"):
            clean_registry.call("need_name", {})

    def test_call_with_wrong_type(self, clean_registry):
        clean_registry.register(
            "need_int", lambda x: x,
            params=[FunctionParam(name="x", param_type="integer")],
        )
        with pytest.raises(ValidationError, match="expected integer"):
            clean_registry.call("need_int", {"x": "not_int"})

    def test_call_with_invalid_enum(self, clean_registry):
        clean_registry.register(
            "choice", lambda c: c,
            params=[
                FunctionParam(name="c", enum=["a", "b"]),
            ],
        )
        with pytest.raises(ValidationError, match="must be one of"):
            clean_registry.call("choice", {"c": "z"})

    def test_call_tracks_metrics(self, registry):
        registry.call("calculator", {"expression": "1+1"})
        func = registry.get("calculator")
        assert func.call_count >= 1
        assert func.last_called > 0

    def test_call_error_handling(self, clean_registry):
        def failing():
            raise RuntimeError("boom")

        clean_registry.register("fail", failing)
        with pytest.raises(FunctionCallError, match="boom"):
            clean_registry.call("fail", {})


class TestAsyncCalling:
    def test_call_async_function(self, clean_registry):
        async def async_double(x: int) -> int:
            return x * 2

        clean_registry.register(
            "async_double", async_double,
            params=[FunctionParam(name="x", param_type="integer")],
        )
        result = clean_registry.call("async_double", {"x": 5})
        assert result == 10

    def test_async_call_method(self, clean_registry):
        async def async_add(a: int, b: int) -> int:
            return a + b

        clean_registry.register(
            "async_add", async_add,
            params=[
                FunctionParam(name="a", param_type="integer"),
                FunctionParam(name="b", param_type="integer"),
            ],
        )
        # Use synchronous call which handles async internally
        result = clean_registry.call("async_add", {"a": 3, "b": 4})
        assert result == 7


class TestRateLimiting:
    def test_rate_limit_exceeded(self, clean_registry):
        clean_registry.register(
            "limited", lambda: "ok",
            rate_limit=2,
        )
        clean_registry.call("limited", {})
        clean_registry.call("limited", {})
        with pytest.raises(RateLimitError):
            clean_registry.call("limited", {})

    def test_no_rate_limit(self, clean_registry):
        clean_registry.register("unlimited", lambda: "ok", rate_limit=0)
        for _ in range(100):
            clean_registry.call("unlimited", {})


class TestOpenAIDispatch:
    def test_dispatch_tool_call(self, registry):
        tool_call = {
            "id": "call_123",
            "function": {
                "name": "calculator",
                "arguments": '{"expression": "10/2"}',
            },
        }
        result = registry.dispatch_openai_tool_call(tool_call)
        assert result["tool_call_id"] == "call_123"
        assert result["role"] == "tool"
        assert "5" in result["content"]

    def test_dispatch_invalid_json(self, registry):
        tool_call = {
            "id": "call_456",
            "function": {
                "name": "calculator",
                "arguments": "not json",
            },
        }
        result = registry.dispatch_openai_tool_call(tool_call)
        assert "error" in result["content"]

    def test_dispatch_unknown_function(self, registry):
        tool_call = {
            "id": "call_789",
            "function": {
                "name": "unknown_func",
                "arguments": "{}",
            },
        }
        result = registry.dispatch_openai_tool_call(tool_call)
        assert "error" in result["content"]


class TestClaudeDispatch:
    def test_dispatch_tool_use(self, registry):
        tool_use = {
            "id": "tu_123",
            "name": "calculator",
            "input": {"expression": "3*3"},
        }
        result = registry.dispatch_claude_tool_use(tool_use)
        assert result["tool_use_id"] == "tu_123"
        assert result["type"] == "tool_result"
        assert "9" in result["content"]

    def test_dispatch_error(self, registry):
        tool_use = {
            "id": "tu_456",
            "name": "unknown",
            "input": {},
        }
        result = registry.dispatch_claude_tool_use(tool_use)
        assert result.get("is_error") is True


class TestMetrics:
    def test_get_metrics(self, registry):
        registry.call("calculator", {"expression": "1+1"})
        metrics = registry.get_metrics()
        assert "calculator" in metrics
        assert metrics["calculator"]["call_count"] >= 1

    def test_execution_log(self, registry):
        registry.call("calculator", {"expression": "2+2"})
        log = registry.get_execution_log()
        assert len(log) >= 1
        assert log[-1]["function"] == "calculator"
        assert log[-1]["success"] is True


class TestEnableDisable:
    def test_enable_disable(self, registry):
        assert registry.disable("calculator")
        func = registry.get("calculator")
        assert not func.enabled
        assert registry.enable("calculator")
        assert func.enabled

    def test_enable_nonexistent(self, registry):
        assert not registry.enable("nonexistent")

    def test_disable_nonexistent(self, registry):
        assert not registry.disable("nonexistent")


class TestBuiltins:
    def test_get_current_time(self, registry):
        result = registry.call("get_current_time", {"timezone": "UTC"})
        assert "T" in result  # ISO format

    def test_calculator_safe(self, registry):
        result = registry.call("calculator", {"expression": "2+3"})
        assert result == "5"

    def test_string_tools_reverse(self, registry):
        result = registry.call("string_tools", {"action": "reverse", "text": "abc"})
        assert result == "cba"

    def test_string_tools_length(self, registry):
        result = registry.call("string_tools", {"action": "length", "text": "hello"})
        assert result == "5"

    def test_string_tools_unknown(self, registry):
        # Enum validation blocks unknown action before reaching handler
        with pytest.raises(ValidationError, match="must be one of"):
            registry.call("string_tools", {"action": "unknown", "text": "x"})

    def test_json_format(self, registry):
        result = registry.call("json_format", {"data": '{"a":1}'})
        parsed = json.loads(result)
        assert parsed == {"a": 1}

    def test_json_format_invalid(self, registry):
        result = registry.call("json_format", {"data": "not json"})
        assert "Invalid JSON" in result
