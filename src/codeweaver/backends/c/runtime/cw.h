/*
 * CodeWeaver C runtime support (header-only, C11).
 *
 * Generated C calls into this header wherever Python semantics differ from
 * C semantics: str() formatting, floor division and modulo, negative and
 * bounds-checked indexing, growable lists and string operations.
 *
 * Memory: values are allocated with cw_alloc and never freed, which matches
 * Python's semantics for the lifetime of a short program but not for a
 * long-running one.  Link against a garbage collector (e.g. Boehm GC, by
 * defining CW_ALLOC to GC_MALLOC) for long-running programs.
 *
 * Errors: an uncaught Python exception becomes cw_raise, which prints a
 * Python-style message and exits with status 1.  C has no exceptions, so
 * try/except is not translated.
 */
#ifndef CW_RUNTIME_H
#define CW_RUNTIME_H

#include <math.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifndef CW_ALLOC
#define CW_ALLOC malloc
#endif

#if defined(__GNUC__) || defined(__clang__)
#define CW_NORETURN __attribute__((noreturn))
#define CW_UNUSED __attribute__((unused))
#else
#define CW_NORETURN
#define CW_UNUSED
#endif

typedef const char* cw_str;

/* ------------------------------------------------------------- errors */

CW_NORETURN static inline void cw_raise(cw_str type, cw_str message) {
  fflush(stdout);
  fprintf(stderr, "Traceback (most recent call last):\n%s: %s\n", type, message ? message : "");
  exit(1);
}

CW_NORETURN static inline void cw_unsupported(cw_str what) {
  cw_raise("NotImplementedError", what);
}

static inline void* cw_alloc(size_t size) {
  void* memory = CW_ALLOC(size ? size : 1);
  if (!memory) cw_raise("MemoryError", "out of memory");
  return memory;
}

/* ------------------------------------------------------------ strings */

static inline cw_str cw_strdup_n(const char* text, size_t length) {
  char* out = (char*)cw_alloc(length + 1);
  memcpy(out, text, length);
  out[length] = '\0';
  return out;
}

static inline cw_str cw_str_i64(int64_t value) {
  char buffer[24];
  int length = snprintf(buffer, sizeof buffer, "%lld", (long long)value);
  return cw_strdup_n(buffer, (size_t)length);
}

/* Python's float repr: the shortest string that round-trips, in fixed
 * notation when the exponent is in [-4, 16) and scientific otherwise. */
static inline cw_str cw_str_f64(double value) {
  if (isnan(value)) return "nan";
  if (isinf(value)) return value > 0 ? "inf" : "-inf";
  if (value == 0) return signbit(value) ? "-0.0" : "0.0";
  char buffer[40];
  int precision = 1;
  for (; precision <= 17; ++precision) {
    snprintf(buffer, sizeof buffer, "%.*e", precision - 1, value);
    if (strtod(buffer, NULL) == value) break;
  }
  /* buffer is [-]d[.ddd]e[+-]XX: split it into digits and exponent. */
  char digits[24];
  int count = 0;
  const char* cursor = buffer;
  bool negative = *cursor == '-';
  if (negative) ++cursor;
  for (; *cursor && *cursor != 'e'; ++cursor) {
    if (*cursor != '.') digits[count++] = *cursor;
  }
  int exponent = atoi(cursor + 1);
  while (count > 1 && digits[count - 1] == '0') --count;
  char out[48];
  int at = 0;
  if (negative) out[at++] = '-';
  if (exponent >= -4 && exponent < 16) {
    if (exponent < 0) {
      out[at++] = '0';
      out[at++] = '.';
      for (int i = 0; i < -exponent - 1; ++i) out[at++] = '0';
      for (int i = 0; i < count; ++i) out[at++] = digits[i];
    } else {
      for (int i = 0; i <= exponent; ++i) out[at++] = i < count ? digits[i] : '0';
      out[at++] = '.';
      if (count > exponent + 1) {
        for (int i = exponent + 1; i < count; ++i) out[at++] = digits[i];
      } else {
        out[at++] = '0';
      }
    }
  } else {
    out[at++] = digits[0];
    if (count > 1) {
      out[at++] = '.';
      for (int i = 1; i < count; ++i) out[at++] = digits[i];
    }
    at += snprintf(out + at, sizeof out - (size_t)at, "e%c%02d", exponent < 0 ? '-' : '+', abs(exponent));
  }
  return cw_strdup_n(out, (size_t)at);
}

static inline cw_str cw_str_bool(bool value) { return value ? "True" : "False"; }
static inline cw_str cw_str_str(cw_str value) { return value ? value : "None"; }

static inline cw_str cw_repr_str(cw_str value) {
  if (!value) return "None";
  size_t length = strlen(value);
  char* out = (char*)cw_alloc(length * 2 + 3);
  size_t at = 0;
  out[at++] = '\'';
  for (size_t i = 0; i < length; ++i) {
    char c = value[i];
    if (c == '\\' || c == '\'') {
      out[at++] = '\\';
      out[at++] = c;
    } else if (c == '\n') {
      out[at++] = '\\';
      out[at++] = 'n';
    } else if (c == '\t') {
      out[at++] = '\\';
      out[at++] = 't';
    } else {
      out[at++] = c;
    }
  }
  out[at++] = '\'';
  out[at] = '\0';
  return out;
}

/* Concatenate `count` strings. */
static inline cw_str cw_cat(int count, ...) {
  va_list args;
  va_start(args, count);
  size_t total = 0;
  for (int i = 0; i < count; ++i) total += strlen(va_arg(args, cw_str));
  va_end(args);
  char* out = (char*)cw_alloc(total + 1);
  size_t at = 0;
  va_start(args, count);
  for (int i = 0; i < count; ++i) {
    cw_str part = va_arg(args, cw_str);
    size_t length = strlen(part);
    memcpy(out + at, part, length);
    at += length;
  }
  va_end(args);
  out[at] = '\0';
  return out;
}

/* print(a, b, c): `count` already-formatted parts, space separated. */
static inline void cw_print(int count, ...) {
  va_list args;
  va_start(args, count);
  for (int i = 0; i < count; ++i) {
    if (i) fputc(' ', stdout);
    fputs(va_arg(args, cw_str), stdout);
  }
  va_end(args);
  fputc('\n', stdout);
}

static inline bool cw_str_eq(cw_str a, cw_str b) {
  if (a == b) return true;
  if (!a || !b) return false;
  return strcmp(a, b) == 0;
}
static inline int cw_str_cmp(cw_str a, cw_str b) { return strcmp(a, b); }
static inline int64_t cw_str_len(cw_str text) { return (int64_t)strlen(text); }
static inline bool cw_str_truthy(cw_str text) { return text && *text; }

static inline cw_str cw_str_at(cw_str text, int64_t index) {
  int64_t length = (int64_t)strlen(text);
  int64_t at = index < 0 ? index + length : index;
  if (at < 0 || at >= length) cw_raise("IndexError", "string index out of range");
  return cw_strdup_n(text + at, 1);
}

static inline cw_str cw_str_concat(cw_str a, cw_str b) { return cw_cat(2, a, b); }

static inline cw_str cw_str_repeat(cw_str text, int64_t count) {
  if (count <= 0) return "";
  size_t length = strlen(text);
  char* out = (char*)cw_alloc(length * (size_t)count + 1);
  for (int64_t i = 0; i < count; ++i) memcpy(out + (size_t)i * length, text, length);
  out[length * (size_t)count] = '\0';
  return out;
}

static inline cw_str cw_str_upper(cw_str text) {
  size_t length = strlen(text);
  char* out = (char*)cw_alloc(length + 1);
  for (size_t i = 0; i <= length; ++i) out[i] = (char)(text[i] >= 'a' && text[i] <= 'z' ? text[i] - 32 : text[i]);
  return out;
}

static inline cw_str cw_str_lower(cw_str text) {
  size_t length = strlen(text);
  char* out = (char*)cw_alloc(length + 1);
  for (size_t i = 0; i <= length; ++i) out[i] = (char)(text[i] >= 'A' && text[i] <= 'Z' ? text[i] + 32 : text[i]);
  return out;
}

static inline bool cw_is_space(char c) { return c == ' ' || (c >= '\t' && c <= '\r'); }

static inline cw_str cw_str_strip(cw_str text) {
  const char* start = text;
  while (*start && cw_is_space(*start)) ++start;
  const char* end = start + strlen(start);
  while (end > start && cw_is_space(end[-1])) --end;
  return cw_strdup_n(start, (size_t)(end - start));
}

static inline bool cw_str_startswith(cw_str text, cw_str prefix) {
  return strncmp(text, prefix, strlen(prefix)) == 0;
}

static inline bool cw_str_endswith(cw_str text, cw_str suffix) {
  size_t a = strlen(text), b = strlen(suffix);
  return b <= a && memcmp(text + a - b, suffix, b) == 0;
}

static inline int64_t cw_str_find(cw_str text, cw_str needle) {
  const char* found = strstr(text, needle);
  return found ? (int64_t)(found - text) : -1;
}

static inline bool cw_str_contains(cw_str text, cw_str needle) { return strstr(text, needle) != NULL; }

static inline cw_str cw_str_replace(cw_str text, cw_str old, cw_str replacement) {
  size_t old_length = strlen(old);
  if (old_length == 0) return text;
  size_t new_length = strlen(replacement), count = 0;
  for (const char* at = strstr(text, old); at; at = strstr(at + old_length, old)) ++count;
  size_t total = strlen(text) + count * new_length - count * old_length;
  char* out = (char*)cw_alloc(total + 1);
  char* write = out;
  const char* read = text;
  for (const char* at = strstr(read, old); at; at = strstr(read, old)) {
    memcpy(write, read, (size_t)(at - read));
    write += at - read;
    memcpy(write, replacement, new_length);
    write += new_length;
    read = at + old_length;
  }
  strcpy(write, read);
  return out;
}

/* ---------------------------------------------------------- arithmetic */

static inline int64_t cw_floordiv_i64(int64_t a, int64_t b) {
  if (b == 0) cw_raise("ZeroDivisionError", "integer division or modulo by zero");
  int64_t q = a / b;
  if ((a % b != 0) && ((a < 0) != (b < 0))) --q;
  return q;
}

static inline int64_t cw_mod_i64(int64_t a, int64_t b) {
  if (b == 0) cw_raise("ZeroDivisionError", "integer division or modulo by zero");
  int64_t r = a % b;
  if (r != 0 && ((r < 0) != (b < 0))) r += b;
  return r;
}

static inline double cw_truediv(double a, double b) {
  if (b == 0) cw_raise("ZeroDivisionError", "division by zero");
  return a / b;
}

static inline double cw_floordiv_f64(double a, double b) {
  if (b == 0) cw_raise("ZeroDivisionError", "float floor division by zero");
  return floor(a / b);
}

static inline double cw_mod_f64(double a, double b) {
  if (b == 0) cw_raise("ZeroDivisionError", "float modulo");
  double r = fmod(a, b);
  if (r != 0 && ((r < 0) != (b < 0))) r += b;
  return r;
}

static inline int64_t cw_pow_i64(int64_t base, int64_t exponent) {
  int64_t result = 1;
  while (exponent > 0) {
    if (exponent & 1) result *= base;
    base *= base;
    exponent >>= 1;
  }
  return result;
}

static inline int64_t cw_abs_i64(int64_t value) { return value < 0 ? -value : value; }

/* round(x): halves go to the even neighbour (rint's default mode). */
static inline int64_t cw_round_i64(double value) { return (int64_t)rint(value); }

static inline double cw_round_f64(double value, int64_t digits) {
  double scale = pow(10.0, (double)digits);
  double rounded = rint(value * scale) / scale;
  return isfinite(rounded) ? rounded : value;
}

static inline int64_t cw_int_from_str(cw_str text) {
  char* end = NULL;
  long long value = strtoll(text, &end, 10);
  while (end && cw_is_space(*end)) ++end;
  if (end == text || (end && *end)) {
    cw_raise("ValueError", cw_cat(2, "invalid literal for int() with base 10: ", cw_repr_str(text)));
  }
  return (int64_t)value;
}

static inline double cw_float_from_str(cw_str text) {
  char* end = NULL;
  double value = strtod(text, &end);
  while (end && cw_is_space(*end)) ++end;
  if (end == text || (end && *end)) {
    cw_raise("ValueError", cw_cat(2, "could not convert string to float: ", cw_repr_str(text)));
  }
  return value;
}

static inline int64_t cw_min_i64(int64_t a, int64_t b) { return a < b ? a : b; }
static inline int64_t cw_max_i64(int64_t a, int64_t b) { return a > b ? a : b; }

/* --------------------------------------------------------------- lists */

static inline int64_t cw_index(int64_t index, int64_t length) {
  int64_t at = index < 0 ? index + length : index;
  if (at < 0 || at >= length) cw_raise("IndexError", "list index out of range");
  return at;
}

static inline int64_t cw_clamp(bool present, int64_t value, int64_t length, int64_t fallback) {
  if (!present) return fallback;
  if (value < 0) value += length;
  if (value < 0) return 0;
  return value > length ? length : value;
}

/* Defines a growable list of T named cw_list_NAME, with Python semantics. */
#define CW_DEFINE_LIST(NAME, T, EQ, REPR)                                                  \
  typedef struct cw_list_##NAME {                                                          \
    int64_t len;                                                                           \
    int64_t cap;                                                                           \
    T* items;                                                                              \
  } cw_list_##NAME;                                                                        \
  CW_UNUSED static inline cw_list_##NAME* cw_list_##NAME##_new(void) {                     \
    cw_list_##NAME* list = (cw_list_##NAME*)cw_alloc(sizeof *list);                        \
    list->len = 0;                                                                         \
    list->cap = 0;                                                                         \
    list->items = NULL;                                                                    \
    return list;                                                                           \
  }                                                                                        \
  CW_UNUSED static inline void cw_list_##NAME##_reserve(cw_list_##NAME* list, int64_t n) { \
    if (n <= list->cap) return;                                                            \
    int64_t cap = list->cap ? list->cap : 4;                                               \
    while (cap < n) cap *= 2;                                                              \
    T* items = (T*)cw_alloc(sizeof(T) * (size_t)cap);                                      \
    if (list->len) memcpy(items, list->items, sizeof(T) * (size_t)list->len);              \
    list->items = items;                                                                   \
    list->cap = cap;                                                                       \
  }                                                                                        \
  CW_UNUSED static inline cw_list_##NAME* cw_list_##NAME##_of(int64_t n, T const* items) { \
    cw_list_##NAME* list = cw_list_##NAME##_new();                                         \
    cw_list_##NAME##_reserve(list, n);                                                     \
    if (n) memcpy(list->items, items, sizeof(T) * (size_t)n);                              \
    list->len = n;                                                                         \
    return list;                                                                           \
  }                                                                                        \
  CW_UNUSED static inline void cw_list_##NAME##_append(cw_list_##NAME* list, T value) {    \
    if (list->len == list->cap) cw_list_##NAME##_reserve(list, list->len + 1);             \
    list->items[list->len++] = value;                                                      \
  }                                                                                        \
  CW_UNUSED static inline T cw_list_##NAME##_get(const cw_list_##NAME* list, int64_t i) {  \
    return list->items[cw_index(i, list->len)];                                            \
  }                                                                                        \
  CW_UNUSED static inline void cw_list_##NAME##_set(cw_list_##NAME* list, int64_t i,       \
                                                    T value) {                             \
    list->items[cw_index(i, list->len)] = value;                                           \
  }                                                                                        \
  CW_UNUSED static inline T cw_list_##NAME##_pop(cw_list_##NAME* list, int64_t i) {        \
    if (list->len == 0) cw_raise("IndexError", "pop from empty list");                     \
    int64_t at = cw_index(i, list->len);                                                   \
    T value = list->items[at];                                                             \
    memmove(list->items + at, list->items + at + 1,                                        \
            sizeof(T) * (size_t)(list->len - at - 1));                                     \
    --list->len;                                                                           \
    return value;                                                                          \
  }                                                                                        \
  CW_UNUSED static inline void cw_list_##NAME##_insert(cw_list_##NAME* list, int64_t i,    \
                                                       T value) {                          \
    int64_t at = i < 0 ? (i + list->len < 0 ? 0 : i + list->len)                           \
                       : (i > list->len ? list->len : i);                                  \
    cw_list_##NAME##_reserve(list, list->len + 1);                                         \
    memmove(list->items + at + 1, list->items + at, sizeof(T) * (size_t)(list->len - at)); \
    list->items[at] = value;                                                               \
    ++list->len;                                                                           \
  }                                                                                        \
  CW_UNUSED static inline bool cw_list_##NAME##_contains(const cw_list_##NAME* list,       \
                                                         T value) {                        \
    for (int64_t i = 0; i < list->len; ++i)                                                \
      if (EQ(list->items[i], value)) return true;                                          \
    return false;                                                                          \
  }                                                                                        \
  CW_UNUSED static inline int64_t cw_list_##NAME##_index(const cw_list_##NAME* list,       \
                                                         T value) {                        \
    for (int64_t i = 0; i < list->len; ++i)                                                \
      if (EQ(list->items[i], value)) return i;                                             \
    cw_raise("ValueError", "value is not in list");                                        \
  }                                                                                        \
  CW_UNUSED static inline int64_t cw_list_##NAME##_count(const cw_list_##NAME* list,       \
                                                         T value) {                        \
    int64_t total = 0;                                                                     \
    for (int64_t i = 0; i < list->len; ++i) total += EQ(list->items[i], value);            \
    return total;                                                                          \
  }                                                                                        \
  CW_UNUSED static inline void cw_list_##NAME##_remove(cw_list_##NAME* list, T value) {    \
    cw_list_##NAME##_pop(list, cw_list_##NAME##_index(list, value));                       \
  }                                                                                        \
  CW_UNUSED static inline void cw_list_##NAME##_extend(cw_list_##NAME* list,               \
                                                       const cw_list_##NAME* other) {      \
    int64_t n = other->len;                                                                \
    cw_list_##NAME##_reserve(list, list->len + n);                                         \
    if (n) memmove(list->items + list->len, other->items, sizeof(T) * (size_t)n);          \
    list->len += n;                                                                        \
  }                                                                                        \
  CW_UNUSED static inline cw_list_##NAME* cw_list_##NAME##_copy(const cw_list_##NAME* l) { \
    return cw_list_##NAME##_of(l->len, l->items);                                          \
  }                                                                                        \
  CW_UNUSED static inline cw_list_##NAME* cw_list_##NAME##_concat(                         \
      const cw_list_##NAME* a, const cw_list_##NAME* b) {                                  \
    cw_list_##NAME* out = cw_list_##NAME##_copy(a);                                        \
    cw_list_##NAME##_extend(out, b);                                                       \
    return out;                                                                            \
  }                                                                                        \
  CW_UNUSED static inline cw_list_##NAME* cw_list_##NAME##_repeat(                         \
      const cw_list_##NAME* list, int64_t count) {                                         \
    cw_list_##NAME* out = cw_list_##NAME##_new();                                          \
    if (count > 0) cw_list_##NAME##_reserve(out, list->len * count);                       \
    for (int64_t i = 0; i < count; ++i) cw_list_##NAME##_extend(out, list);                \
    return out;                                                                            \
  }                                                                                        \
  CW_UNUSED static inline void cw_list_##NAME##_reverse(cw_list_##NAME* list) {            \
    for (int64_t i = 0, j = list->len - 1; i < j; ++i, --j) {                              \
      T held = list->items[i];                                                             \
      list->items[i] = list->items[j];                                                     \
      list->items[j] = held;                                                               \
    }                                                                                      \
  }                                                                                        \
  CW_UNUSED static inline cw_list_##NAME* cw_list_##NAME##_slice(                          \
      const cw_list_##NAME* list, bool has_lower, int64_t lower, bool has_upper,           \
      int64_t upper, int64_t step) {                                                       \
    if (step == 0) cw_raise("ValueError", "slice step cannot be zero");                    \
    cw_list_##NAME* out = cw_list_##NAME##_new();                                          \
    int64_t n = list->len;                                                                 \
    if (step > 0) {                                                                        \
      int64_t first = cw_clamp(has_lower, lower, n, 0);                                    \
      int64_t last = cw_clamp(has_upper, upper, n, n);                                     \
      for (int64_t i = first; i < last; i += step) cw_list_##NAME##_append(out, list->items[i]); \
    } else {                                                                               \
      int64_t first = has_lower ? (lower < 0 ? lower + n : (lower >= n ? n - 1 : lower))   \
                                : n - 1;                                                   \
      int64_t last = has_upper ? (upper < 0 ? upper + n : upper) : -1;                     \
      if (last < -1) last = -1;                                                            \
      for (int64_t i = first; i > last; i += step) cw_list_##NAME##_append(out, list->items[i]); \
    }                                                                                      \
    return out;                                                                            \
  }                                                                                        \
  CW_UNUSED static inline cw_str cw_list_##NAME##_repr(const cw_list_##NAME* list) {       \
    size_t total = 2, at = 0;                                                              \
    cw_str* parts = (cw_str*)cw_alloc(sizeof(cw_str) * (size_t)(list->len ? list->len : 1)); \
    for (int64_t i = 0; i < list->len; ++i) {                                              \
      parts[i] = REPR(list->items[i]);                                                     \
      total += strlen(parts[i]) + 2;                                                       \
    }                                                                                      \
    char* out = (char*)cw_alloc(total + 1);                                                \
    out[at++] = '[';                                                                       \
    for (int64_t i = 0; i < list->len; ++i) {                                              \
      if (i) {                                                                             \
        out[at++] = ',';                                                                   \
        out[at++] = ' ';                                                                   \
      }                                                                                    \
      size_t length = strlen(parts[i]);                                                    \
      memcpy(out + at, parts[i], length);                                                  \
      at += length;                                                                        \
    }                                                                                      \
    out[at++] = ']';                                                                       \
    out[at] = '\0';                                                                        \
    return out;                                                                            \
  }

#define CW_EQ_VALUE(a, b) ((a) == (b))
#define CW_EQ_STR(a, b) cw_str_eq((a), (b))
static inline cw_str cw_repr_ptr(void* value) { return value ? "<object>" : "None"; }

CW_DEFINE_LIST(i64, int64_t, CW_EQ_VALUE, cw_str_i64)
CW_DEFINE_LIST(f64, double, CW_EQ_VALUE, cw_str_f64)
CW_DEFINE_LIST(bool, bool, CW_EQ_VALUE, cw_str_bool)
CW_DEFINE_LIST(str, cw_str, CW_EQ_STR, cw_repr_str)
CW_DEFINE_LIST(ptr, void*, CW_EQ_VALUE, cw_repr_ptr)

static int cw_compare_i64(const void* a, const void* b) {
  int64_t x = *(const int64_t*)a, y = *(const int64_t*)b;
  return (x > y) - (x < y);
}
static int cw_compare_f64(const void* a, const void* b) {
  double x = *(const double*)a, y = *(const double*)b;
  return (x > y) - (x < y);
}
static int cw_compare_str(const void* a, const void* b) {
  return strcmp(*(const cw_str*)a, *(const cw_str*)b);
}

/* list.sort() is stable in Python; qsort is not, but equal numbers and
 * equal strings are indistinguishable, so the result is the same. */
static inline void cw_list_i64_sort(cw_list_i64* list, bool reverse) {
  if (list->len) qsort(list->items, (size_t)list->len, sizeof(int64_t), cw_compare_i64);
  if (reverse) cw_list_i64_reverse(list);
}
static inline void cw_list_f64_sort(cw_list_f64* list, bool reverse) {
  if (list->len) qsort(list->items, (size_t)list->len, sizeof(double), cw_compare_f64);
  if (reverse) cw_list_f64_reverse(list);
}
static inline void cw_list_str_sort(cw_list_str* list, bool reverse) {
  if (list->len) qsort(list->items, (size_t)list->len, sizeof(cw_str), cw_compare_str);
  if (reverse) cw_list_str_reverse(list);
}

static inline int64_t cw_list_i64_sum(const cw_list_i64* list) {
  int64_t total = 0;
  for (int64_t i = 0; i < list->len; ++i) total += list->items[i];
  return total;
}
static inline double cw_list_f64_sum(const cw_list_f64* list) {
  double total = 0;
  for (int64_t i = 0; i < list->len; ++i) total += list->items[i];
  return total;
}
static inline int64_t cw_list_i64_min(const cw_list_i64* list) {
  if (!list->len) cw_raise("ValueError", "min() arg is an empty sequence");
  int64_t best = list->items[0];
  for (int64_t i = 1; i < list->len; ++i) if (list->items[i] < best) best = list->items[i];
  return best;
}
static inline int64_t cw_list_i64_max(const cw_list_i64* list) {
  if (!list->len) cw_raise("ValueError", "max() arg is an empty sequence");
  int64_t best = list->items[0];
  for (int64_t i = 1; i < list->len; ++i) if (list->items[i] > best) best = list->items[i];
  return best;
}
static inline double cw_list_f64_min(const cw_list_f64* list) {
  if (!list->len) cw_raise("ValueError", "min() arg is an empty sequence");
  double best = list->items[0];
  for (int64_t i = 1; i < list->len; ++i) if (list->items[i] < best) best = list->items[i];
  return best;
}
static inline double cw_list_f64_max(const cw_list_f64* list) {
  if (!list->len) cw_raise("ValueError", "max() arg is an empty sequence");
  double best = list->items[0];
  for (int64_t i = 1; i < list->len; ++i) if (list->items[i] > best) best = list->items[i];
  return best;
}

static inline cw_list_i64* cw_range(int64_t start, int64_t stop, int64_t step) {
  if (step == 0) cw_raise("ValueError", "range() arg 3 must not be zero");
  cw_list_i64* out = cw_list_i64_new();
  for (int64_t i = start; step > 0 ? i < stop : i > stop; i += step) cw_list_i64_append(out, i);
  return out;
}

static inline cw_list_str* cw_str_split(cw_str text, cw_str separator) {
  cw_list_str* out = cw_list_str_new();
  if (separator == NULL) {
    const char* at = text;
    while (*at) {
      while (*at && cw_is_space(*at)) ++at;
      if (!*at) break;
      const char* start = at;
      while (*at && !cw_is_space(*at)) ++at;
      cw_list_str_append(out, cw_strdup_n(start, (size_t)(at - start)));
    }
    return out;
  }
  size_t length = strlen(separator);
  if (length == 0) cw_raise("ValueError", "empty separator");
  const char* start = text;
  for (const char* at = strstr(start, separator); at; at = strstr(start, separator)) {
    cw_list_str_append(out, cw_strdup_n(start, (size_t)(at - start)));
    start = at + length;
  }
  cw_list_str_append(out, start);
  return out;
}

static inline cw_str cw_str_join(cw_str separator, const cw_list_str* parts) {
  size_t total = 0, length = strlen(separator);
  for (int64_t i = 0; i < parts->len; ++i) total += strlen(parts->items[i]) + length;
  char* out = (char*)cw_alloc(total + 1);
  size_t at = 0;
  for (int64_t i = 0; i < parts->len; ++i) {
    if (i) {
      memcpy(out + at, separator, length);
      at += length;
    }
    size_t piece = strlen(parts->items[i]);
    memcpy(out + at, parts->items[i], piece);
    at += piece;
  }
  out[at] = '\0';
  return out;
}

/* str(x) for any value the generator can type. */
#define CW_STR(x)                       \
  _Generic((x),                         \
      bool: cw_str_bool,                \
      int: cw_str_i64,                  \
      long: cw_str_i64,                 \
      long long: cw_str_i64,            \
      double: cw_str_f64,               \
      char*: cw_str_str,                \
      const char*: cw_str_str,          \
      cw_list_i64*: cw_list_i64_repr,   \
      cw_list_f64*: cw_list_f64_repr,   \
      cw_list_bool*: cw_list_bool_repr, \
      cw_list_str*: cw_list_str_repr,   \
      cw_list_ptr*: cw_list_ptr_repr)(x)

#endif /* CW_RUNTIME_H */
