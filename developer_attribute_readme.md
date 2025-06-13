# Home Assistant Entity Attribute Guide

## The Dual Attribute Pattern in Home Assistant

Home Assistant uses two different patterns for entity attributes that can be confusing and lead to subtle bugs. This guide
explains these patterns to help you avoid common pitfalls when developing integrations.

## Two Attribute Patterns

### 1. Protected Attributes (`_attr_*`)

For most entity attributes (state, name, icon, etc.), Home Assistant uses protected attributes with an `_attr_` prefix. These
are managed by the `CachedProperties` metaclass which provides automatic caching and invalidation:

```python
class MyEntity(Entity):
    """My entity implementation."""

    # Protected attributes with _attr_ prefix
    _attr_name: str | None = None
    _attr_icon: str | None = None
    _attr_device_class: str | None = None
    _attr_extra_state_attributes: dict[str, Any] = {}
```

The `CachedProperties` metaclass:

- Automatically creates property getters/setters for `_attr_*` attributes
- Manages caching of property values
- Invalidates cache when attributes are modified
- Handles type annotations correctly

### 2. Direct Public Attributes

While most attributes use the protected `_attr_` pattern, there are a few special cases that use direct public attributes:

1. `entity_description`: The primary example, used for storing entity descriptions
2. `unique_id`: In some cases, used for direct entity identification
3. `platform`: Used to identify the platform an entity belongs to
4. `registry_entry`: Used for entity registry entries
5. `hass`: Reference to the Home Assistant instance

Example:

```python
# These are set directly without _attr_ prefix
self.entity_description = description
self.unique_id = f"{serial_number}_{entity_id}"
self.platform = platform
```

The reason these attributes are public varies:

1. They represent fundamental identity or configuration that shouldn't be overridden
2. They are part of the public API contract
3. They are frequently accessed by the core framework
4. They are used in property getter fallback chains

## Type Annotations and Custom EntityDescriptions

When extending an entity description with custom attributes, type checkers will often complain when you try to access the
custom attributes. This is because the type system only sees the base class type (e.g., `BinarySensorEntityDescription`),
not your custom type.

### Example Issue

```python
# Your custom entity description class with added attributes
@dataclass(frozen=True)
class MyCustomEntityDescription(BinarySensorEntityDescription):
    """Custom entity description with extra attributes."""
    value_fn: Callable[[Any], bool]  # Custom attribute

# Your entity class
class MyEntity(BinarySensorEntity):
    def __init__(self, description: MyCustomEntityDescription):
        self.entity_description = description  # Type is seen as BinarySensorEntityDescription

    def update(self):
        # Type error! BinarySensorEntityDescription has no attribute 'value_fn'
        result = self.entity_description.value_fn(self.data)
```

### Proper Solutions

There are several ways to handle this typing issue, each with their own advantages:

#### 1. Store Direct References (Recommended)

The cleanest solution is to store direct references to the custom attributes during initialization:

```python
def __init__(self, description: MyCustomEntityDescription):
    super().__init__()
    self.entity_description = description

    # Store a direct reference to value_fn to avoid type issues later
    self._value_fn = description.value_fn

def update(self):
    # Use the directly stored reference - no type issues!
    result = self._value_fn(self.data)
```

This approach:

- Works correctly even with optimized Python (`-O` flag)
- Has no runtime overhead
- Keeps code clean and readable
- Preserves proper type information

#### 2. Use `typing.cast`

For cases where storing a direct reference isn't feasible, use `typing.cast`:

```python
from typing import cast

def update(self):
    # Cast to our specific type for type-checking - this has no runtime overhead
    description = cast(MyCustomEntityDescription, self.entity_description)
    result = description.value_fn(self.data)
```

This approach:

- Satisfies the type checker
- Has zero runtime overhead (cast is removed during compilation)
- Doesn't protect against actual type errors at runtime

#### 3. Use Helper Properties or Methods

Create helper properties or methods that handle the typing:

```python
@property
def my_description(self) -> MyCustomEntityDescription:
    """Return the entity description as the specific type."""
    return self.entity_description  # type: ignore[return-value]

def update(self):
    result = self.my_description.value_fn(self.data)
```

### What NOT to Do: Using Assertions

**Do not use assertions for type checking:**

```python
def update(self):
    description = self.entity_description
    assert isinstance(description, MyCustomEntityDescription)  # BAD PRACTICE!
    result = description.value_fn(self.data)
```

This approach is problematic because:

1. Assertions are completely removed when Python runs with optimizations enabled (`-O` flag)
2. This can lead to runtime errors in production environments
3. Security linters like Bandit will flag this as a vulnerability (B101)

## When to Use Each Pattern

- **Use `self._attr_*`** for most entity attributes (name, state, device_class, etc.)
- **Use `self.entity_description`** specifically for the entity description

## Common Pitfalls

### The `entity_description` Trap

The most common mistake is using `self._attr_entity_description = description` instead of `self.entity_description = description`.

This can cause subtle bugs because:

1. The entity will initialize without errors
2. Basic functionality might work
3. But properties that fall back to the entity description (like device_class) won't work correctly
4. Runtime errors may occur when trying to access methods or properties of the entity description

### Example of What Not to Do

```python
# INCORRECT - Will cause bugs
def __init__(self, coordinator, description):
    super().__init__(coordinator)
    self._attr_entity_description = description  # WRONG!
    self._attr_device_class = description.device_class
```

### Correct Implementation

```python
# CORRECT
def __init__(self, coordinator, description):
    super().__init__(coordinator)
    self.entity_description = description  # Correct!
    self._attr_device_class = description.device_class  # This is also correct
```

## How Home Assistant Uses entity_description

Understanding how Home Assistant uses `entity_description` internally helps explain why it's treated differently:

```python
# From Home Assistant's Entity class
@cached_property
def device_class(self) -> str | None:
    """Return the class of this entity."""
    if hasattr(self, "_attr_device_class"):
        return self._attr_device_class
    if hasattr(self, "entity_description"):  # Fallback to entity_description
        return self.entity_description.device_class
    return None
```

This pattern appears throughout Home Assistant's code. The framework first checks the direct attribute, then falls back to the
entity description if available.

## Why The Dual Pattern Exists

Home Assistant's approach evolved over time:

1. **Historical Evolution**: Older code used direct attributes, newer code uses the `_attr_` pattern
2. **Special Role**: `entity_description` serves as a container of defaults and is a public API
3. **Cached Properties**: The `_attr_` pattern works with Home Assistant's property caching system
4. **Fallback Chain**: Property getters use a fallback chain: `_attr_*` → `entity_description.*` → default

### Why `entity_description` is a Public Attribute

Home Assistant likely uses a public attribute for `entity_description` for several reasons:

1. **API Contract**: The entity description represents a public API contract that is meant to be preserved and
   directly accessed
2. **Composition vs. Inheritance**: It emphasizes composition (an entity has a description) rather than inheritance (an
   entity is a description)
3. **Interoperability**: Allows for more flexible interoperability between integrations and the core
   framework
4. **Serialization**: May facilitate easier serialization/deserialization when needed
5. **Accessor Pattern**: Other parts of Home Assistant can access the description directly without needing accessor methods

The inconsistency between `entity_description` and other `_attr_*` attributes may simply be an architectural decision made at
different points in Home Assistant's development history.

## Best Practices

1. **Use `self._attr_*` for entity attributes** - This automatically gets you:

   - Protected attribute storage
   - Cached property getters/setters (via the `CachedProperties` metaclass)
   - Proper type annotation handling
   - Automatic cache invalidation

2. **Use `self.entity_description`** (never `self._attr_entity_description`) for entity descriptions

3. **When extending `Entity` classes:**

   - Check the parent class implementation to understand the attribute pattern
   - Use the same pattern as the parent class for consistency
   - Include proper type annotations to help catch issues earlier

4. **For custom entity descriptions:**

   - Store direct references to custom description attributes in your entity's `__init__` method
   - Use proper type annotations to avoid type checker issues
   - Test property access, especially for device_class and other properties that might come from entity_description

5. **For custom properties** (when you need something beyond the standard `_attr_*` pattern):

   ```python
   @cached_property
   def custom_property(self) -> str:
       """Return a computed property value."""
       return self._compute_value()
   ```

## Summary

Home Assistant's dual attribute pattern can be confusing, but following these guidelines will help avoid subtle bugs:

- Use `self._attr_*` for most attributes (this automatically includes caching)
- Use `self.entity_description` (no underscore prefix) for the entity description
- Store direct references to custom description attributes to avoid type issues

This inconsistency in the framework's design is unfortunately something developers need to be aware of when building integrations.
