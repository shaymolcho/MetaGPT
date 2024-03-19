#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2023/01/12 17:07
@Author  : garylin2099
@File    : tool_registry.py
"""
from __future__ import annotations

import importlib.util
import inspect
import os
from collections import defaultdict

import yaml
from pydantic import BaseModel

from metagpt.const import TOOL_SCHEMA_PATH
from metagpt.logs import logger
from metagpt.tools.tool_convert import convert_code_to_tool_schema
from metagpt.tools.tool_data_type import Tool, ToolSchema


class ToolRegistry(BaseModel):
    tools: dict = {}
    tools_by_tags: dict = defaultdict(dict)  # two-layer k-v, {tag: {tool_name: {...}, ...}, ...}

    def register_tool(
        self,
        tool_name,
        tool_path,
        schema_path="",
        tool_code="",
        tags=None,
        tool_source_object=None,
        include_functions=None,
        verbose=False,
    ):
        if self.has_tool(tool_name):
            return

        schema_path = schema_path or TOOL_SCHEMA_PATH / f"{tool_name}.yml"

        schemas = make_schema(tool_source_object, include_functions, schema_path)

        if not schemas:
            return

        schemas["tool_path"] = tool_path  # corresponding code file path of the tool
        try:
            ToolSchema(**schemas)  # validation
        except Exception:
            pass
            # logger.warning(
            #     f"{tool_name} schema not conforms to required format, but will be used anyway. Mismatch: {e}"
            # )
        tags = tags or []
        tool = Tool(name=tool_name, path=tool_path, schemas=schemas, code=tool_code, tags=tags)
        self.tools[tool_name] = tool
        for tag in tags:
            self.tools_by_tags[tag].update({tool_name: tool})
        if verbose:
            logger.info(f"{tool_name} registered")
            logger.info(f"schema made at {str(schema_path)}, can be used for checking")

    def has_tool(self, key: str) -> Tool:
        return key in self.tools

    def get_tool(self, key) -> Tool:
        return self.tools.get(key)

    def get_tools_by_tag(self, key) -> dict[str, Tool]:
        return self.tools_by_tags.get(key, {})

    def get_all_tools(self) -> dict[str, Tool]:
        return self.tools

    def has_tool_tag(self, key) -> bool:
        return key in self.tools_by_tags

    def get_tool_tags(self) -> list[str]:
        return list(self.tools_by_tags.keys())


# Registry instance
TOOL_REGISTRY = ToolRegistry()


def register_tool(tags: list[str] = None, schema_path: str = "", **kwargs):
    """register a tool to registry"""

    def decorator(cls):
        # Get the file path where the function / class is defined and the source code
        file_path = inspect.getfile(cls)
        if "metagpt" in file_path:
            # split to handle ../metagpt/metagpt/tools/... where only metapgt/tools/... is needed
            file_path = "metagpt" + file_path.split("metagpt")[-1]
        source_code = inspect.getsource(cls)

        TOOL_REGISTRY.register_tool(
            tool_name=cls.__name__,
            tool_path=file_path,
            schema_path=schema_path,
            tool_code=source_code,
            tags=tags,
            tool_source_object=cls,
            **kwargs,
        )
        return cls

    return decorator


def make_schema(tool_source_object, include, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)  # Create the necessary directories
    try:
        schema = convert_code_to_tool_schema(tool_source_object, include=include)
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(schema, f, sort_keys=False)
        # import json
        # with open(str(path).replace("yml", "json"), "w", encoding="utf-8") as f:
        #     json.dump(schema, f, ensure_ascii=False, indent=4)
    except Exception as e:
        schema = {}
        logger.error(f"Fail to make schema: {e}")

    return schema


def validate_tool_names(tools: list[str]) -> dict[str, Tool]:
    assert isinstance(tools, list), "tools must be a list of str"
    valid_tools = {}
    for key in tools:
        # one can define either tool names OR tool tags OR tool path, take union to get the whole set
        # if tool paths are provided, they will be registered on the fly
        if os.path.isdir(key) or os.path.isfile(key):
            valid_tools.update(register_tools_from_path(key))
        elif TOOL_REGISTRY.has_tool(key):
            valid_tools.update({key: TOOL_REGISTRY.get_tool(key)})
        elif TOOL_REGISTRY.has_tool_tag(key):
            valid_tools.update(TOOL_REGISTRY.get_tools_by_tag(key))
        else:
            logger.warning(f"invalid tool name or tool type name: {key}, skipped")
    return valid_tools


def load_module_from_file(filepath):
    module_name = os.path.splitext(os.path.basename(filepath))[0]
    spec = importlib.util.spec_from_file_location(module_name, filepath)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def register_tools_from_file(file_path) -> dict[str, Tool]:
    registered_tools = {}
    module = load_module_from_file(file_path)
    for name, obj in inspect.getmembers(module):
        if inspect.isclass(obj) or inspect.isfunction(obj):
            if obj.__module__ == module.__name__:
                # excluding imported classes and functions, register only those defined in the file
                if "metagpt" in file_path:
                    # split to handle ../metagpt/metagpt/tools/... where only metapgt/tools/... is needed
                    file_path = "metagpt" + file_path.split("metagpt")[-1]

                TOOL_REGISTRY.register_tool(
                    tool_name=name,
                    tool_path=file_path,
                    tool_code="",  # inspect.getsource(obj) will resulted in TypeError, skip it for now
                    tool_source_object=obj,
                )
                registered_tools.update({name: TOOL_REGISTRY.get_tool(name)})

    return registered_tools


def register_tools_from_path(path) -> dict[str, Tool]:
    tools_registered = {}
    if os.path.isfile(path) and path.endswith(".py"):
        # Path is a Python file
        tools_registered.update(register_tools_from_file(path))
    elif os.path.isdir(path):
        # Path is a directory
        for root, _, files in os.walk(path):
            for file in files:
                if file.endswith(".py"):
                    file_path = os.path.join(root, file)
                    tools_registered.update(register_tools_from_file(file_path))
    return tools_registered
