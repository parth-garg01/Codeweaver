// CodeWeaver C++ runtime support.
//
// Generated C++ calls into this header wherever Python semantics differ from
// C++ semantics: floor division and modulo signs, negative indexing, bounds and
// key errors, truthiness, str() formatting, and the builtin exception
// hierarchy.  Every function is inline and header-only so the optimiser sees
// through it; none of it allocates unless the Python operation it models does.
//
// Define CW_UNCHECKED before including this header to turn indexing into raw,
// unchecked access.  That trades Python's IndexError for maximum speed and is
// only appropriate once the translated program is known to be correct.
#pragma once

#include <algorithm>
#include <cctype>
#include <charconv>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <exception>
#include <functional>
#include <initializer_list>
#include <iostream>
#include <limits>
#include <memory>
#include <numbers>
#include <numeric>
#include <optional>
#include <sstream>
#include <string>
#include <string_view>
#include <tuple>
#include <type_traits>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <variant>
#include <vector>

namespace cw {

using namespace std::string_literals;

// ---------------------------------------------------------------------------
// Object model

/// Root of every generated class.  It makes the hierarchy polymorphic (so
/// isinstance can use dynamic_pointer_cast) and lets a method hand out a
/// shared pointer to itself, which is what Python's `self` is.
class Object : public std::enable_shared_from_this<Object> {
 public:
  virtual ~Object() = default;
};

/// `self` used as a value: a shared pointer of the receiver's own type.
template <class T>
std::shared_ptr<T> self(T* receiver) {
  return std::static_pointer_cast<T>(receiver->shared_from_this());
}

/// Stand-in for a construct CodeWeaver could not translate.  It converts to
/// any type so the surrounding code still compiles, and throws if reached.
struct Unsupported;

// ---------------------------------------------------------------------------
// Exceptions: Python's builtin hierarchy, thrown and caught by value.

class BaseException : public std::exception {
 public:
  BaseException() = default;
  explicit BaseException(std::string message) : message_(std::move(message)) {}
  template <class A, class B, class... Rest>
  BaseException(const A& a, const B& b, const Rest&... rest);
  const char* what() const noexcept override { return message_.c_str(); }
  const std::string& message() const noexcept { return message_; }
  virtual const char* type_name() const noexcept { return "BaseException"; }

 private:
  std::string message_;
};

#define CW_DEFINE_EXCEPTION(Name, Base)                              \
  class Name : public Base {                                         \
   public:                                                           \
    using Base::Base;                                                \
    const char* type_name() const noexcept override { return #Name; } \
  };

CW_DEFINE_EXCEPTION(Exception, BaseException)
CW_DEFINE_EXCEPTION(ArithmeticError, Exception)
CW_DEFINE_EXCEPTION(ZeroDivisionError, ArithmeticError)
CW_DEFINE_EXCEPTION(OverflowError, ArithmeticError)
CW_DEFINE_EXCEPTION(AssertionError, Exception)
CW_DEFINE_EXCEPTION(AttributeError, Exception)
CW_DEFINE_EXCEPTION(EOFError, Exception)
CW_DEFINE_EXCEPTION(ImportError, Exception)
CW_DEFINE_EXCEPTION(LookupError, Exception)
CW_DEFINE_EXCEPTION(IndexError, LookupError)
CW_DEFINE_EXCEPTION(KeyError, LookupError)
CW_DEFINE_EXCEPTION(MemoryError, Exception)
CW_DEFINE_EXCEPTION(NameError, Exception)
CW_DEFINE_EXCEPTION(OSError, Exception)
CW_DEFINE_EXCEPTION(FileNotFoundError, OSError)
CW_DEFINE_EXCEPTION(PermissionError, OSError)
CW_DEFINE_EXCEPTION(TimeoutError, OSError)
CW_DEFINE_EXCEPTION(RuntimeError, Exception)
CW_DEFINE_EXCEPTION(NotImplementedError, RuntimeError)
CW_DEFINE_EXCEPTION(RecursionError, RuntimeError)
CW_DEFINE_EXCEPTION(StopIteration, Exception)
CW_DEFINE_EXCEPTION(TypeError, Exception)
CW_DEFINE_EXCEPTION(ValueError, Exception)
CW_DEFINE_EXCEPTION(UnicodeError, ValueError)

#undef CW_DEFINE_EXCEPTION

struct Unsupported {
  std::string what;
  template <class T>
  operator T() const {  // NOLINT(google-explicit-constructor)
    throw NotImplementedError("CodeWeaver could not translate: " + what);
  }
};

inline Unsupported unsupported(std::string what) { return Unsupported{std::move(what)}; }

/// Runs a callable when the enclosing scope exits, which is how Python's
/// `finally` is expressed: it runs on normal exit, on return and on unwind.
template <class F>
class Finally {
 public:
  explicit Finally(F body) : body_(std::move(body)) {}
  Finally(const Finally&) = delete;
  Finally& operator=(const Finally&) = delete;
  ~Finally() noexcept(false) { body_(); }

 private:
  F body_;
};

// ---------------------------------------------------------------------------
// An insertion-ordered dictionary.  Python dicts iterate in insertion order and
// programs rely on it; std::unordered_map does not, so translating to it would
// change output.  Lookups are hashed; erase is O(n) in the number of entries.

template <class K, class V>
class dict {
 public:
  using value_type = std::pair<K, V>;
  using iterator = typename std::vector<value_type>::iterator;
  using const_iterator = typename std::vector<value_type>::const_iterator;

  dict() = default;
  dict(std::initializer_list<value_type> entries) {
    for (const auto& entry : entries) (*this)[entry.first] = entry.second;
  }

  V& operator[](const K& key) {
    auto found = index_.find(key);
    if (found != index_.end()) return items_[found->second].second;
    index_.emplace(key, items_.size());
    items_.emplace_back(key, V{});
    return items_.back().second;
  }

  V& at(const K& key) {
    auto found = index_.find(key);
    if (found == index_.end()) throw KeyError("key not found");
    return items_[found->second].second;
  }
  const V& at(const K& key) const {
    auto found = index_.find(key);
    if (found == index_.end()) throw KeyError("key not found");
    return items_[found->second].second;
  }

  bool contains(const K& key) const { return index_.find(key) != index_.end(); }
  std::size_t size() const noexcept { return items_.size(); }
  bool empty() const noexcept { return items_.empty(); }
  void clear() noexcept {
    items_.clear();
    index_.clear();
  }

  bool erase(const K& key) {
    auto found = index_.find(key);
    if (found == index_.end()) return false;
    std::size_t position = found->second;
    items_.erase(items_.begin() + static_cast<std::ptrdiff_t>(position));
    index_.erase(found);
    for (auto& entry : index_) {
      if (entry.second > position) --entry.second;
    }
    return true;
  }

  iterator begin() noexcept { return items_.begin(); }
  iterator end() noexcept { return items_.end(); }
  const_iterator begin() const noexcept { return items_.begin(); }
  const_iterator end() const noexcept { return items_.end(); }

  bool operator==(const dict& other) const {
    if (size() != other.size()) return false;
    for (const auto& [key, value] : items_) {
      if (!other.contains(key) || !(other.at(key) == value)) return false;
    }
    return true;
  }

 private:
  std::vector<value_type> items_;
  std::unordered_map<K, std::size_t> index_;
};

template <class T>
using set = std::unordered_set<T>;

// ---------------------------------------------------------------------------
// Type traits used to pick the Python behaviour for a C++ type.

template <class T>
struct is_vector : std::false_type {};
template <class T, class A>
struct is_vector<std::vector<T, A>> : std::true_type {};

template <class T>
struct is_dict : std::false_type {};
template <class K, class V>
struct is_dict<dict<K, V>> : std::true_type {};

template <class T>
struct is_set : std::false_type {};
template <class T, class H, class E, class A>
struct is_set<std::unordered_set<T, H, E, A>> : std::true_type {};

template <class T>
struct is_shared_ptr : std::false_type {};
template <class T>
struct is_shared_ptr<std::shared_ptr<T>> : std::true_type {};

template <class T>
struct is_optional : std::false_type {};
template <class T>
struct is_optional<std::optional<T>> : std::true_type {};

template <class T>
struct is_tuple : std::false_type {};
template <class... T>
struct is_tuple<std::tuple<T...>> : std::true_type {};

template <class T>
using bare = std::remove_cv_t<std::remove_reference_t<T>>;

// ---------------------------------------------------------------------------
// Truthiness

template <class T>
bool truthy(const T& value) {
  using U = bare<T>;
  if constexpr (std::is_same_v<U, bool>) {
    return value;
  } else if constexpr (std::is_arithmetic_v<U>) {
    return value != 0;
  } else if constexpr (std::is_same_v<U, std::string> || std::is_same_v<U, std::string_view>) {
    return !value.empty();
  } else if constexpr (is_shared_ptr<U>::value || std::is_pointer_v<U>) {
    return value != nullptr;
  } else if constexpr (std::is_same_v<U, std::nullptr_t>) {
    return false;
  } else if constexpr (is_optional<U>::value) {
    return value.has_value() && truthy(*value);
  } else if constexpr (is_tuple<U>::value) {
    return std::tuple_size_v<U> != 0;
  } else {
    return !value.empty();
  }
}

// ---------------------------------------------------------------------------
// Arithmetic with Python semantics

template <class A, class B>
auto truediv(A a, B b) -> double {
  if (b == 0) throw ZeroDivisionError("division by zero");
  return static_cast<double>(a) / static_cast<double>(b);
}

template <class A, class B>
auto floordiv(A a, B b) {
  if constexpr (std::is_floating_point_v<A> || std::is_floating_point_v<B>) {
    if (b == 0) throw ZeroDivisionError("float floor division by zero");
    return std::floor(static_cast<double>(a) / static_cast<double>(b));
  } else {
    using R = std::common_type_t<A, B, std::int64_t>;
    if (b == 0) throw ZeroDivisionError("integer division or modulo by zero");
    R q = static_cast<R>(a) / static_cast<R>(b);
    R r = static_cast<R>(a) % static_cast<R>(b);
    return (r != 0 && ((r < 0) != (b < 0))) ? q - 1 : q;
  }
}

template <class A, class B>
auto mod(A a, B b) {
  if constexpr (std::is_floating_point_v<A> || std::is_floating_point_v<B>) {
    if (b == 0) throw ZeroDivisionError("float modulo");
    double r = std::fmod(static_cast<double>(a), static_cast<double>(b));
    return (r != 0 && ((r < 0) != (b < 0))) ? r + static_cast<double>(b) : r;
  } else {
    using R = std::common_type_t<A, B, std::int64_t>;
    if (b == 0) throw ZeroDivisionError("integer division or modulo by zero");
    R r = static_cast<R>(a) % static_cast<R>(b);
    return (r != 0 && ((r < 0) != (b < 0))) ? r + static_cast<R>(b) : r;
  }
}

template <class A, class B>
auto pow(A base, B exponent) {
  if constexpr (std::is_integral_v<A> && std::is_integral_v<B>) {
    if (exponent < 0) {
      // Python returns a float for a negative integer exponent.
      return static_cast<std::int64_t>(std::pow(static_cast<double>(base), static_cast<double>(exponent)));
    }
    std::int64_t result = 1;
    std::int64_t factor = static_cast<std::int64_t>(base);
    std::int64_t remaining = static_cast<std::int64_t>(exponent);
    while (remaining > 0) {
      if (remaining & 1) result *= factor;
      factor *= factor;
      remaining >>= 1;
    }
    return result;
  } else {
    return std::pow(static_cast<double>(base), static_cast<double>(exponent));
  }
}

template <class T>
auto abs(T value) {
  if constexpr (std::is_same_v<T, bool>) {
    return static_cast<std::int64_t>(value);
  } else {
    return value < 0 ? -value : value;
  }
}

/// Python's round(): halves go to the even neighbour.
template <class T>
std::int64_t round(T value) {
  if constexpr (std::is_integral_v<T>) {
    return static_cast<std::int64_t>(value);
  } else {
    return static_cast<std::int64_t>(std::nearbyint(static_cast<double>(value)));
  }
}
template <class T>
double round(T value, std::int64_t digits) {
  double scale = std::pow(10.0, static_cast<double>(digits));
  return std::nearbyint(static_cast<double>(value) * scale) / scale;
}

// ---------------------------------------------------------------------------
// str() and repr()

inline std::string str(const std::string& value) { return value; }
inline std::string str(std::string_view value) { return std::string(value); }
inline std::string str(const char* value) { return std::string(value); }
inline std::string str(bool value) { return value ? "True" : "False"; }
inline std::string str(char value) { return std::string(1, value); }
inline std::string str(std::nullptr_t) { return "None"; }
inline std::string str(std::nullopt_t) { return "None"; }

template <class T, std::enable_if_t<std::is_integral_v<T> && !std::is_same_v<T, bool> && !std::is_same_v<T, char>, int> = 0>
std::string str(T value) {
  return std::to_string(value);
}

/// Python's float repr: the shortest string that round-trips, written in
/// fixed notation for exponents in [-4, 16) and scientific otherwise.
inline std::string str(double value) {
  if (std::isnan(value)) return "nan";
  if (std::isinf(value)) return value > 0 ? "inf" : "-inf";
  if (value == 0) return std::signbit(value) ? "-0.0" : "0.0";
  char buffer[64];
  auto result = std::to_chars(buffer, buffer + sizeof(buffer), value, std::chars_format::scientific);
  std::string scientific(buffer, result.ptr);
  auto e = scientific.find('e');
  std::string mantissa = scientific.substr(0, e);
  int exponent = std::stoi(scientific.substr(e + 1));
  bool negative = mantissa[0] == '-';
  if (negative) mantissa.erase(0, 1);
  std::string digits;
  for (char c : mantissa) {
    if (c != '.') digits.push_back(c);
  }
  std::string out;
  if (exponent >= -4 && exponent < 16) {
    if (exponent < 0) {
      out = "0." + std::string(static_cast<std::size_t>(-exponent - 1), '0') + digits;
    } else if (static_cast<std::size_t>(exponent) + 1 >= digits.size()) {
      out = digits + std::string(static_cast<std::size_t>(exponent) + 1 - digits.size(), '0') + ".0";
    } else {
      out = digits.substr(0, static_cast<std::size_t>(exponent) + 1) + "." +
            digits.substr(static_cast<std::size_t>(exponent) + 1);
    }
  } else {
    out = digits.substr(0, 1);
    if (digits.size() > 1) out += "." + digits.substr(1);
    char exp_text[8];
    std::snprintf(exp_text, sizeof(exp_text), "e%c%02d", exponent < 0 ? '-' : '+', std::abs(exponent));
    out += exp_text;
  }
  return negative ? "-" + out : out;
}
inline std::string str(float value) { return str(static_cast<double>(value)); }

inline std::string str(const BaseException& error) { return error.message(); }

template <class T>
std::string repr(const T& value);

inline std::string repr(const std::string& value) {
  std::string out = "'";
  for (char c : value) {
    switch (c) {
      case '\\': out += "\\\\"; break;
      case '\'': out += "\\'"; break;
      case '\n': out += "\\n"; break;
      case '\t': out += "\\t"; break;
      case '\r': out += "\\r"; break;
      default: out.push_back(c);
    }
  }
  return out + "'";
}
inline std::string repr(const char* value) { return repr(std::string(value)); }

template <class T>
std::string str(const std::shared_ptr<T>& value);
template <class T>
std::string str(const std::optional<T>& value);
template <class T, class A>
std::string str(const std::vector<T, A>& values);
template <class T, class H, class E, class A>
std::string str(const std::unordered_set<T, H, E, A>& values);
template <class K, class V>
std::string str(const dict<K, V>& values);
template <class... T>
std::string str(const std::tuple<T...>& values);

template <class T>
std::string repr(const T& value) {
  return str(value);
}

template <class T>
std::string str(const std::shared_ptr<T>& value) {
  if (!value) return "None";
  // A class that defines __str__ or __repr__ is printed through it.
  if constexpr (requires { value->to_str(); }) {
    return value->to_str();
  } else if constexpr (requires { value->to_repr(); }) {
    return value->to_repr();
  }
  std::ostringstream out;
  out << "<object at " << static_cast<const void*>(value.get()) << ">";
  return out.str();
}

template <class T>
std::string str(const std::optional<T>& value) {
  return value.has_value() ? str(*value) : "None";
}

template <class T, class A>
std::string str(const std::vector<T, A>& values) {
  std::string out = "[";
  bool first = true;
  for (const auto& item : values) {
    if (!first) out += ", ";
    first = false;
    out += repr(item);
  }
  return out + "]";
}

template <class T, class H, class E, class A>
std::string str(const std::unordered_set<T, H, E, A>& values) {
  if (values.empty()) return "set()";
  std::string out = "{";
  bool first = true;
  for (const auto& item : values) {
    if (!first) out += ", ";
    first = false;
    out += repr(item);
  }
  return out + "}";
}

template <class K, class V>
std::string str(const dict<K, V>& values) {
  std::string out = "{";
  bool first = true;
  for (const auto& [key, value] : values) {
    if (!first) out += ", ";
    first = false;
    out += repr(key) + ": " + repr(value);
  }
  return out + "}";
}

template <class... T>
std::string str(const std::tuple<T...>& values) {
  std::string out = "(";
  std::size_t index = 0;
  std::apply(
      [&](const auto&... item) {
        ((out += (index++ ? ", " : "") + repr(item)), ...);
      },
      values);
  if (sizeof...(T) == 1) out += ",";
  return out + ")";
}

template <class A, class B, class... Rest>
BaseException::BaseException(const A& a, const B& b, const Rest&... rest)
    : message_(str(std::make_tuple(a, b, rest...))) {}

/// String concatenation for f-strings: appends each part, converting
/// non-strings with str(), into one buffer.
inline void append_to(std::string& out, const std::string& part) { out += part; }
inline void append_to(std::string& out, const char* part) { out += part; }
template <class T>
void append_to(std::string& out, const T& part) {
  out += str(part);
}
template <class... Parts>
std::string cat(const Parts&... parts) {
  std::string out;
  (append_to(out, parts), ...);
  return out;
}

template <class... Args>
void print(const Args&... args) {
  std::string line;
  std::size_t index = 0;
  ((line += (index++ ? " " : ""), append_to(line, args)), ...);
  line.push_back('\n');
  std::fwrite(line.data(), 1, line.size(), stdout);
}

// ---------------------------------------------------------------------------
// Conversions

inline std::int64_t to_int(const std::string& text) {
  std::size_t start = text.find_first_not_of(" \t\n\r");
  std::size_t end = text.find_last_not_of(" \t\n\r");
  if (start == std::string::npos) throw ValueError("invalid literal for int() with base 10: " + repr(text));
  std::int64_t value = 0;
  const char* first = text.data() + start;
  const char* last = text.data() + end + 1;
  if (*first == '+') ++first;
  auto result = std::from_chars(first, last, value);
  if (result.ec != std::errc() || result.ptr != last) {
    throw ValueError("invalid literal for int() with base 10: " + repr(text));
  }
  return value;
}
template <class T, std::enable_if_t<std::is_arithmetic_v<T>, int> = 0>
std::int64_t to_int(T value) {
  return static_cast<std::int64_t>(value);  // truncates toward zero, as int() does
}

inline double to_float(const std::string& text) {
  try {
    std::size_t used = 0;
    double value = std::stod(text, &used);
    if (text.find_first_not_of(" \t\n\r", used) != std::string::npos) throw std::invalid_argument(text);
    return value;
  } catch (const std::exception&) {
    throw ValueError("could not convert string to float: " + repr(text));
  }
}
template <class T, std::enable_if_t<std::is_arithmetic_v<T>, int> = 0>
double to_float(T value) {
  return static_cast<double>(value);
}

inline std::int64_t ord(const std::string& text) {
  if (text.size() != 1) throw TypeError("ord() expected a character");
  return static_cast<unsigned char>(text[0]);
}
inline std::string chr(std::int64_t code) { return std::string(1, static_cast<char>(code)); }

// ---------------------------------------------------------------------------
// Sequences

template <class C>
std::int64_t len(const C& container) {
  if constexpr (is_shared_ptr<C>::value) {
    return static_cast<std::int64_t>(container->len_());  // a class defining __len__
  } else {
    return static_cast<std::int64_t>(container.size());
  }
}

inline std::size_t normalise_index(std::int64_t index, std::size_t size) {
  std::int64_t position = index < 0 ? index + static_cast<std::int64_t>(size) : index;
#ifndef CW_UNCHECKED
  if (position < 0 || position >= static_cast<std::int64_t>(size)) throw IndexError("index out of range");
#endif
  return static_cast<std::size_t>(position);
}

template <class T, class A>
T& at(std::vector<T, A>& values, std::int64_t index) {
  return values[normalise_index(index, values.size())];
}
template <class T, class A>
const T& at(const std::vector<T, A>& values, std::int64_t index) {
  return values[normalise_index(index, values.size())];
}
inline std::vector<bool>::reference at(std::vector<bool>& values, std::int64_t index) {
  return values[normalise_index(index, values.size())];
}
inline bool at(const std::vector<bool>& values, std::int64_t index) {
  return values[normalise_index(index, values.size())];
}
inline std::string at(const std::string& text, std::int64_t index) {
  return std::string(1, text[normalise_index(index, text.size())]);
}
template <class K, class V, class Key>
V& at(dict<K, V>& values, const Key& key) {
  return values.at(key);
}
template <class K, class V, class Key>
const V& at(const dict<K, V>& values, const Key& key) {
  return values.at(key);
}

/// Python's slice clamping: out-of-range bounds are clipped, never an error.
inline std::size_t clamp_bound(std::optional<std::int64_t> bound, std::size_t size, std::size_t fallback) {
  if (!bound.has_value()) return fallback;
  std::int64_t value = *bound < 0 ? *bound + static_cast<std::int64_t>(size) : *bound;
  if (value < 0) return 0;
  if (value > static_cast<std::int64_t>(size)) return size;
  return static_cast<std::size_t>(value);
}

inline constexpr std::nullopt_t None = std::nullopt;

template <class S>
S slice(const S& values, std::optional<std::int64_t> lower, std::optional<std::int64_t> upper,
        std::optional<std::int64_t> step = std::nullopt) {
  std::int64_t stride = step.value_or(1);
  if (stride == 0) throw ValueError("slice step cannot be zero");
  std::size_t size = values.size();
  S out;
  if (stride > 0) {
    std::size_t first = clamp_bound(lower, size, 0);
    std::size_t last = clamp_bound(upper, size, size);
    if (stride == 1) {
      if (first < last) out.assign(values.begin() + static_cast<std::ptrdiff_t>(first), values.begin() + static_cast<std::ptrdiff_t>(last));
      return out;
    }
    for (std::size_t i = first; i < last; i += static_cast<std::size_t>(stride)) out.push_back(values[i]);
    return out;
  }
  std::int64_t isize = static_cast<std::int64_t>(size);
  std::int64_t first = lower.has_value() ? (*lower < 0 ? *lower + isize : *lower) : isize - 1;
  std::int64_t last = upper.has_value() ? (*upper < 0 ? *upper + isize : *upper) : -1;
  first = std::min(first, isize - 1);
  last = std::max<std::int64_t>(last, -1);
  for (std::int64_t i = first; i > last; i += stride) out.push_back(values[static_cast<std::size_t>(i)]);
  return out;
}

template <class C, class T>
bool contains(const C& container, const T& item) {
  using U = bare<C>;
  if constexpr (std::is_same_v<U, std::string>) {
    return container.find(item) != std::string::npos;
  } else if constexpr (requires { container->contains_(item); }) {
    return container->contains_(item);
  } else {
    return std::find(container.begin(), container.end(), item) != container.end();
  }
}
template <class K, class V, class T>
bool contains(const dict<K, V>& container, const T& item) {
  return container.contains(item);
}
template <class T, class H, class E, class A, class Item>
bool contains(const std::unordered_set<T, H, E, A>& container, const Item& item) {
  return container.count(item) != 0;
}

template <class T, class A>
std::vector<T, A> concat(const std::vector<T, A>& left, const std::vector<T, A>& right) {
  std::vector<T, A> out;
  out.reserve(left.size() + right.size());
  out.insert(out.end(), left.begin(), left.end());
  out.insert(out.end(), right.begin(), right.end());
  return out;
}

template <class S>
S repeat(const S& values, std::int64_t count) {
  S out;
  if (count <= 0) return out;
  out.reserve(values.size() * static_cast<std::size_t>(count));
  for (std::int64_t i = 0; i < count; ++i) out.insert(out.end(), values.begin(), values.end());
  return out;
}

inline std::vector<std::int64_t> range(std::int64_t stop) {
  std::vector<std::int64_t> out;
  if (stop > 0) out.reserve(static_cast<std::size_t>(stop));
  for (std::int64_t i = 0; i < stop; ++i) out.push_back(i);
  return out;
}
inline std::vector<std::int64_t> range(std::int64_t start, std::int64_t stop, std::int64_t step = 1) {
  if (step == 0) throw ValueError("range() arg 3 must not be zero");
  std::vector<std::int64_t> out;
  for (std::int64_t i = start; step > 0 ? i < stop : i > stop; i += step) out.push_back(i);
  return out;
}

// list methods ---------------------------------------------------------------

template <class T, class A, class U>
void append(std::vector<T, A>& values, U&& item) {
  values.push_back(std::forward<U>(item));
}
template <class T, class A, class C>
void extend(std::vector<T, A>& values, const C& items) {
  values.insert(values.end(), items.begin(), items.end());
}
template <class T, class A, class U>
void insert(std::vector<T, A>& values, std::int64_t index, U&& item) {
  std::int64_t size = static_cast<std::int64_t>(values.size());
  std::int64_t position = index < 0 ? std::max<std::int64_t>(index + size, 0) : std::min(index, size);
  values.insert(values.begin() + position, std::forward<U>(item));
}
template <class T, class A>
T pop(std::vector<T, A>& values) {
  if (values.empty()) throw IndexError("pop from empty list");
  T item = std::move(values.back());
  values.pop_back();
  return item;
}
template <class T, class A>
T pop(std::vector<T, A>& values, std::int64_t index) {
  if (values.empty()) throw IndexError("pop from empty list");
  std::size_t position = normalise_index(index, values.size());
  T item = std::move(values[position]);
  values.erase(values.begin() + static_cast<std::ptrdiff_t>(position));
  return item;
}
template <class T, class A, class U>
void remove(std::vector<T, A>& values, const U& item) {
  auto found = std::find(values.begin(), values.end(), item);
  if (found == values.end()) throw ValueError("list.remove(x): x not in list");
  values.erase(found);
}
template <class T, class A, class U>
std::int64_t index(const std::vector<T, A>& values, const U& item) {
  auto found = std::find(values.begin(), values.end(), item);
  if (found == values.end()) throw ValueError("value is not in list");
  return static_cast<std::int64_t>(found - values.begin());
}
template <class T, class A, class U>
std::int64_t count(const std::vector<T, A>& values, const U& item) {
  return static_cast<std::int64_t>(std::count(values.begin(), values.end(), item));
}
template <class C>
void clear(C& values) {
  values.clear();
}
template <class C>
C copy(const C& values) {
  return values;
}
template <class T, class A>
void reverse(std::vector<T, A>& values) {
  std::reverse(values.begin(), values.end());
}
template <class T, class A>
void sort(std::vector<T, A>& values, bool reverse = false) {
  if (reverse) {
    std::stable_sort(values.begin(), values.end(), [](const T& a, const T& b) { return b < a; });
  } else {
    std::stable_sort(values.begin(), values.end());
  }
}
template <class T, class A, class Key>
void sort_by(std::vector<T, A>& values, Key key, bool reverse = false) {
  std::stable_sort(values.begin(), values.end(), [&](const T& a, const T& b) {
    return reverse ? key(b) < key(a) : key(a) < key(b);
  });
}

// Builtins over iterables -----------------------------------------------------

template <class C>
auto list(const C& items) {
  using T = bare<decltype(*std::begin(items))>;
  return std::vector<T>(std::begin(items), std::end(items));
}
/// Iterating a dict yields its keys, in insertion order.
template <class K, class V>
std::vector<K> list(const dict<K, V>& entries) {
  std::vector<K> out;
  out.reserve(entries.size());
  for (const auto& entry : entries) out.push_back(entry.first);
  return out;
}
inline std::vector<std::string> list(const std::string& text) {
  std::vector<std::string> out;
  out.reserve(text.size());
  for (char c : text) out.emplace_back(1, c);
  return out;
}
template <class C>
auto to_set(const C& items) {
  using T = bare<decltype(*std::begin(items))>;
  return std::unordered_set<T>(std::begin(items), std::end(items));
}

template <class C>
auto sorted(const C& items, bool reverse = false) {
  auto out = list(items);
  sort(out, reverse);
  return out;
}
template <class C, class Key>
auto sorted_by(const C& items, Key key, bool reverse = false) {
  auto out = list(items);
  sort_by(out, key, reverse);
  return out;
}

template <class C>
auto sum(const C& items) {
  using T = bare<decltype(*std::begin(items))>;
  using R = std::conditional_t<std::is_floating_point_v<T>, double, std::int64_t>;
  R total = 0;
  for (const auto& item : items) total += item;
  return total;
}

template <class A, class B, class... Rest>
auto min(const A& a, const B& b, const Rest&... rest) {
  using R = std::common_type_t<A, B, Rest...>;
  R best = b < a ? static_cast<R>(b) : static_cast<R>(a);
  ((best = rest < best ? static_cast<R>(rest) : best), ...);
  return best;
}
template <class A, class B, class... Rest>
auto max(const A& a, const B& b, const Rest&... rest) {
  using R = std::common_type_t<A, B, Rest...>;
  R best = a < b ? static_cast<R>(b) : static_cast<R>(a);
  ((best = best < rest ? static_cast<R>(rest) : best), ...);
  return best;
}
template <class C>
auto min(const C& items) {
  auto found = std::min_element(std::begin(items), std::end(items));
  if (found == std::end(items)) throw ValueError("min() arg is an empty sequence");
  return *found;
}
template <class C>
auto max(const C& items) {
  auto found = std::max_element(std::begin(items), std::end(items));
  if (found == std::end(items)) throw ValueError("max() arg is an empty sequence");
  return *found;
}

template <class C>
bool any(const C& items) {
  for (const auto& item : items) {
    if (truthy(item)) return true;
  }
  return false;
}
template <class C>
bool all(const C& items) {
  for (const auto& item : items) {
    if (!truthy(item)) return false;
  }
  return true;
}

// Comprehensions: one clause, an optional filter, evaluated eagerly. --------

template <class T = void, class C, class F>
auto comprehend(const C& items, F element) {
  using E = std::conditional_t<std::is_void_v<T>, bare<std::invoke_result_t<F, decltype(*std::begin(items))>>, T>;
  std::vector<E> out;
  if constexpr (requires { items.size(); }) out.reserve(items.size());
  for (const auto& item : items) out.push_back(element(item));
  return out;
}
template <class T = void, class C, class P, class F>
auto comprehend_if(const C& items, P predicate, F element) {
  using E = std::conditional_t<std::is_void_v<T>, bare<std::invoke_result_t<F, decltype(*std::begin(items))>>, T>;
  std::vector<E> out;
  for (const auto& item : items) {
    if (truthy(predicate(item))) out.push_back(element(item));
  }
  return out;
}
template <class T = void, class C, class F>
auto set_comprehend(const C& items, F element) {
  using E = std::conditional_t<std::is_void_v<T>, bare<std::invoke_result_t<F, decltype(*std::begin(items))>>, T>;
  std::unordered_set<E> out;
  for (const auto& item : items) out.insert(element(item));
  return out;
}
template <class T = void, class C, class P, class F>
auto set_comprehend_if(const C& items, P predicate, F element) {
  using E = std::conditional_t<std::is_void_v<T>, bare<std::invoke_result_t<F, decltype(*std::begin(items))>>, T>;
  std::unordered_set<E> out;
  for (const auto& item : items) {
    if (truthy(predicate(item))) out.insert(element(item));
  }
  return out;
}
template <class C, class FK, class FV>
auto dict_comprehend(const C& items, FK key, FV value) {
  using K = bare<std::invoke_result_t<FK, decltype(*std::begin(items))>>;
  using V = bare<std::invoke_result_t<FV, decltype(*std::begin(items))>>;
  dict<K, V> out;
  for (const auto& item : items) out[key(item)] = value(item);
  return out;
}
template <class C, class P, class FK, class FV>
auto dict_comprehend_if(const C& items, P predicate, FK key, FV value) {
  using K = bare<std::invoke_result_t<FK, decltype(*std::begin(items))>>;
  using V = bare<std::invoke_result_t<FV, decltype(*std::begin(items))>>;
  dict<K, V> out;
  for (const auto& item : items) {
    if (truthy(predicate(item))) out[key(item)] = value(item);
  }
  return out;
}

// dict methods ----------------------------------------------------------------

template <class K, class V, class Key>
std::optional<V> get(const dict<K, V>& values, const Key& key) {
  if (!values.contains(key)) return std::nullopt;
  return values.at(key);
}
template <class K, class V, class Key, class D>
V get(const dict<K, V>& values, const Key& key, const D& fallback) {
  return values.contains(key) ? values.at(key) : static_cast<V>(fallback);
}
template <class K, class V>
std::vector<K> keys(const dict<K, V>& values) {
  std::vector<K> out;
  out.reserve(values.size());
  for (const auto& entry : values) out.push_back(entry.first);
  return out;
}
template <class K, class V>
std::vector<V> values(const dict<K, V>& entries) {
  std::vector<V> out;
  out.reserve(entries.size());
  for (const auto& entry : entries) out.push_back(entry.second);
  return out;
}
template <class K, class V>
std::vector<std::tuple<K, V>> items(const dict<K, V>& entries) {
  std::vector<std::tuple<K, V>> out;
  out.reserve(entries.size());
  for (const auto& entry : entries) out.emplace_back(entry.first, entry.second);
  return out;
}
template <class K, class V, class Key>
V pop(dict<K, V>& values, const Key& key) {
  V item = values.at(key);
  values.erase(key);
  return item;
}
template <class K, class V, class Key, class D>
V setdefault(dict<K, V>& values, const Key& key, const D& fallback) {
  if (!values.contains(key)) values[key] = fallback;
  return values.at(key);
}
template <class K, class V>
void update(dict<K, V>& values, const dict<K, V>& other) {
  for (const auto& [key, value] : other) values[key] = value;
}

// set methods -----------------------------------------------------------------

template <class T, class U>
void add(std::unordered_set<T>& values, U&& item) {
  values.insert(std::forward<U>(item));
}
template <class T, class U>
void remove(std::unordered_set<T>& values, const U& item) {
  if (values.erase(item) == 0) throw KeyError("element not in set");
}
template <class T, class U>
void discard(std::unordered_set<T>& values, const U& item) {
  values.erase(item);
}
template <class T>
void update(std::unordered_set<T>& values, const std::unordered_set<T>& other) {
  values.insert(other.begin(), other.end());
}
template <class T>
std::unordered_set<T> union_(const std::unordered_set<T>& a, const std::unordered_set<T>& b) {
  std::unordered_set<T> out = a;
  out.insert(b.begin(), b.end());
  return out;
}
template <class T>
std::unordered_set<T> intersection(const std::unordered_set<T>& a, const std::unordered_set<T>& b) {
  std::unordered_set<T> out;
  for (const auto& item : a) {
    if (b.count(item)) out.insert(item);
  }
  return out;
}
template <class T>
std::unordered_set<T> difference(const std::unordered_set<T>& a, const std::unordered_set<T>& b) {
  std::unordered_set<T> out;
  for (const auto& item : a) {
    if (!b.count(item)) out.insert(item);
  }
  return out;
}

// str methods -----------------------------------------------------------------

inline std::string upper(std::string text) {
  for (char& c : text) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
  return text;
}
inline std::string lower(std::string text) {
  for (char& c : text) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
  return text;
}
inline std::string strip(const std::string& text, const std::string& chars = " \t\n\r\v\f") {
  std::size_t start = text.find_first_not_of(chars);
  if (start == std::string::npos) return "";
  std::size_t end = text.find_last_not_of(chars);
  return text.substr(start, end - start + 1);
}
inline std::string lstrip(const std::string& text, const std::string& chars = " \t\n\r\v\f") {
  std::size_t start = text.find_first_not_of(chars);
  return start == std::string::npos ? "" : text.substr(start);
}
inline std::string rstrip(const std::string& text, const std::string& chars = " \t\n\r\v\f") {
  std::size_t end = text.find_last_not_of(chars);
  return end == std::string::npos ? "" : text.substr(0, end + 1);
}
inline bool startswith(const std::string& text, const std::string& prefix) {
  return text.size() >= prefix.size() && text.compare(0, prefix.size(), prefix) == 0;
}
inline bool endswith(const std::string& text, const std::string& suffix) {
  return text.size() >= suffix.size() && text.compare(text.size() - suffix.size(), suffix.size(), suffix) == 0;
}
inline std::int64_t find(const std::string& text, const std::string& needle) {
  std::size_t found = text.find(needle);
  return found == std::string::npos ? -1 : static_cast<std::int64_t>(found);
}
inline std::int64_t rfind(const std::string& text, const std::string& needle) {
  std::size_t found = text.rfind(needle);
  return found == std::string::npos ? -1 : static_cast<std::int64_t>(found);
}
inline std::int64_t index(const std::string& text, const std::string& needle) {
  std::size_t found = text.find(needle);
  if (found == std::string::npos) throw ValueError("substring not found");
  return static_cast<std::int64_t>(found);
}
inline std::int64_t count(const std::string& text, const std::string& needle) {
  if (needle.empty()) return static_cast<std::int64_t>(text.size()) + 1;
  std::int64_t total = 0;
  for (std::size_t at = text.find(needle); at != std::string::npos; at = text.find(needle, at + needle.size())) ++total;
  return total;
}
inline std::string replace(const std::string& text, const std::string& old, const std::string& with) {
  if (old.empty()) return text;
  std::string out;
  std::size_t start = 0;
  for (std::size_t at = text.find(old); at != std::string::npos; at = text.find(old, start)) {
    out.append(text, start, at - start);
    out += with;
    start = at + old.size();
  }
  out.append(text, start, std::string::npos);
  return out;
}
inline std::vector<std::string> split(const std::string& text) {
  std::vector<std::string> out;
  std::size_t i = 0;
  while (i < text.size()) {
    while (i < text.size() && std::isspace(static_cast<unsigned char>(text[i]))) ++i;
    if (i >= text.size()) break;
    std::size_t start = i;
    while (i < text.size() && !std::isspace(static_cast<unsigned char>(text[i]))) ++i;
    out.push_back(text.substr(start, i - start));
  }
  return out;
}
inline std::vector<std::string> split(const std::string& text, const std::string& separator) {
  if (separator.empty()) throw ValueError("empty separator");
  std::vector<std::string> out;
  std::size_t start = 0;
  for (std::size_t at = text.find(separator); at != std::string::npos; at = text.find(separator, start)) {
    out.push_back(text.substr(start, at - start));
    start = at + separator.size();
  }
  out.push_back(text.substr(start));
  return out;
}
template <class C>
std::string join(const std::string& separator, const C& parts) {
  std::string out;
  bool first = true;
  for (const auto& part : parts) {
    if (!first) out += separator;
    first = false;
    out += part;
  }
  return out;
}
template <class Pred>
bool all_chars(const std::string& text, Pred pred) {
  if (text.empty()) return false;
  for (char c : text) {
    if (!pred(static_cast<unsigned char>(c))) return false;
  }
  return true;
}
inline bool isdigit(const std::string& text) { return all_chars(text, [](unsigned char c) { return std::isdigit(c) != 0; }); }
inline bool isalpha(const std::string& text) { return all_chars(text, [](unsigned char c) { return std::isalpha(c) != 0; }); }
inline bool isalnum(const std::string& text) { return all_chars(text, [](unsigned char c) { return std::isalnum(c) != 0; }); }
inline bool isspace(const std::string& text) { return all_chars(text, [](unsigned char c) { return std::isspace(c) != 0; }); }
inline bool isupper(const std::string& text) {
  bool cased = false;
  for (char c : text) {
    if (std::islower(static_cast<unsigned char>(c))) return false;
    if (std::isupper(static_cast<unsigned char>(c))) cased = true;
  }
  return cased;
}
inline bool islower(const std::string& text) {
  bool cased = false;
  for (char c : text) {
    if (std::isupper(static_cast<unsigned char>(c))) return false;
    if (std::islower(static_cast<unsigned char>(c))) cased = true;
  }
  return cased;
}
inline std::string capitalize(const std::string& text) {
  std::string out = lower(text);
  if (!out.empty()) out[0] = static_cast<char>(std::toupper(static_cast<unsigned char>(out[0])));
  return out;
}
inline std::string title(const std::string& text) {
  std::string out = text;
  bool start = true;
  for (char& c : out) {
    unsigned char u = static_cast<unsigned char>(c);
    c = static_cast<char>(start ? std::toupper(u) : std::tolower(u));
    start = !std::isalpha(u);
  }
  return out;
}
inline std::string zfill(const std::string& text, std::int64_t width) {
  if (static_cast<std::int64_t>(text.size()) >= width) return text;
  std::size_t pad = static_cast<std::size_t>(width) - text.size();
  if (!text.empty() && (text[0] == '-' || text[0] == '+')) return text.substr(0, 1) + std::string(pad, '0') + text.substr(1);
  return std::string(pad, '0') + text;
}

// ---------------------------------------------------------------------------
// Program entry: what an uncaught exception looks like, as Python prints it.

template <class F>
int run_main(F body) {
  std::ios::sync_with_stdio(false);
  try {
    body();
  } catch (const BaseException& error) {
    std::fflush(stdout);
    std::fprintf(stderr, "Traceback (most recent call last):\n%s: %s\n", error.type_name(), error.what());
    return 1;
  }
  return 0;
}

}  // namespace cw
