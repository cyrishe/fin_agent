from typing import Any, Dict, List


class SchemaValidationError(ValueError):
    pass


class SchemaValidator:
    """
    Minimal local JSON schema validator.
    Supports the subset currently needed by our tool and skill schemas:
    - type
    - properties
    - required
    - additionalProperties=false
    - items
    - const
    - $ref within local $defs
    """

    def validate(self, payload: Any, schema: Dict[str, Any]) -> None:
        self._validate_node(payload, schema, root_schema=schema, path="$")

    def _resolve_ref(self, ref: str, root_schema: Dict[str, Any]) -> Dict[str, Any]:
        if not ref.startswith("#/"):
            raise SchemaValidationError(f"unsupported ref: {ref}")
        node: Any = root_schema
        for part in ref[2:].split("/"):
            if not isinstance(node, dict) or part not in node:
                raise SchemaValidationError(f"invalid ref: {ref}")
            node = node[part]
        if not isinstance(node, dict):
            raise SchemaValidationError(f"invalid ref target: {ref}")
        return node

    def _validate_node(self, payload: Any, schema: Dict[str, Any], *, root_schema: Dict[str, Any], path: str) -> None:
        if "$ref" in schema:
            resolved = self._resolve_ref(str(schema["$ref"]), root_schema)
            self._validate_node(payload, resolved, root_schema=root_schema, path=path)
            return

        if "const" in schema and payload != schema["const"]:
            raise SchemaValidationError(f"{path}: expected const {schema['const']!r}")

        expected_type = schema.get("type")
        if expected_type is not None:
            self._validate_type(payload, expected_type, path)

        if expected_type == "object":
            self._validate_object(payload, schema, root_schema=root_schema, path=path)
        elif expected_type == "array":
            self._validate_array(payload, schema, root_schema=root_schema, path=path)

    def _validate_type(self, payload: Any, expected_type: Any, path: str) -> None:
        type_list = expected_type if isinstance(expected_type, list) else [expected_type]
        if any(self._matches_type(payload, item) for item in type_list):
            return
        raise SchemaValidationError(f"{path}: expected type {type_list}, got {type(payload).__name__}")

    @staticmethod
    def _matches_type(payload: Any, expected_type: str) -> bool:
        if expected_type == "object":
            return isinstance(payload, dict)
        if expected_type == "array":
            return isinstance(payload, list)
        if expected_type == "string":
            return isinstance(payload, str)
        if expected_type == "boolean":
            return isinstance(payload, bool)
        if expected_type == "integer":
            return isinstance(payload, int) and not isinstance(payload, bool)
        if expected_type == "number":
            return (isinstance(payload, int) and not isinstance(payload, bool)) or isinstance(payload, float)
        if expected_type == "null":
            return payload is None
        return True

    def _validate_object(self, payload: Dict[str, Any], schema: Dict[str, Any], *, root_schema: Dict[str, Any], path: str) -> None:
        required = schema.get("required", [])
        for key in required:
            if key not in payload:
                raise SchemaValidationError(f"{path}: missing required field '{key}'")

        properties = schema.get("properties", {})
        additional_allowed = schema.get("additionalProperties", True)
        for key, value in payload.items():
            if key in properties:
                self._validate_node(value, properties[key], root_schema=root_schema, path=f"{path}.{key}")
            elif additional_allowed is False:
                raise SchemaValidationError(f"{path}: unexpected field '{key}'")

    def _validate_array(self, payload: List[Any], schema: Dict[str, Any], *, root_schema: Dict[str, Any], path: str) -> None:
        items_schema = schema.get("items")
        if items_schema is None:
            return
        if isinstance(items_schema, list):
            min_items = schema.get("minItems")
            max_items = schema.get("maxItems")
            if min_items is not None and len(payload) < min_items:
                raise SchemaValidationError(f"{path}: expected at least {min_items} items")
            if max_items is not None and len(payload) > max_items:
                raise SchemaValidationError(f"{path}: expected at most {max_items} items")
            for index, item_schema in enumerate(items_schema):
                if index >= len(payload):
                    break
                self._validate_node(payload[index], item_schema, root_schema=root_schema, path=f"{path}[{index}]")
            return
        for index, item in enumerate(payload):
            self._validate_node(item, items_schema, root_schema=root_schema, path=f"{path}[{index}]")
