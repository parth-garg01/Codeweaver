// CodeWeaver Java runtime support.
//
// Generated Java calls into this class wherever Python semantics differ from
// Java semantics: str() formatting, negative indexing, slicing, true division
// by zero, list and dict helpers, and the few Python exceptions that have no
// Java counterpart.  Everything is static and allocation-free unless the Python
// operation it models allocates.
package cwrt;

import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.NoSuchElementException;
import java.util.Set;
import java.util.function.Function;

@SuppressWarnings({"serial", "varargs"})
public final class Py {
    private Py() {}

    // ------------------------------------------------------------ exceptions
    // Python exceptions with no direct Java counterpart.  The others map onto
    // Java's own: ValueError -> IllegalArgumentException, IndexError ->
    // IndexOutOfBoundsException, KeyError -> NoSuchElementException,
    // ZeroDivisionError -> ArithmeticException, NotImplementedError ->
    // UnsupportedOperationException.

    public static class TypeError extends RuntimeException {
        public TypeError() { super(); }
        public TypeError(String message) { super(message); }
    }

    public static class RuntimeError extends RuntimeException {
        public RuntimeError() { super(); }
        public RuntimeError(String message) { super(message); }
    }

    public static class RecursionError extends RuntimeError {
        public RecursionError() { super(); }
        public RecursionError(String message) { super(message); }
    }

    public static class AttributeError extends RuntimeException {
        public AttributeError() { super(); }
        public AttributeError(String message) { super(message); }
    }

    public static class NameError extends RuntimeException {
        public NameError() { super(); }
        public NameError(String message) { super(message); }
    }

    public static class LookupError extends RuntimeException {
        public LookupError() { super(); }
        public LookupError(String message) { super(message); }
    }

    public static class OSError extends RuntimeException {
        public OSError() { super(); }
        public OSError(String message) { super(message); }
    }

    public static class FileNotFoundError extends OSError {
        public FileNotFoundError() { super(); }
        public FileNotFoundError(String message) { super(message); }
    }

    public static class PermissionError extends OSError {
        public PermissionError() { super(); }
        public PermissionError(String message) { super(message); }
    }

    public static class TimeoutError extends OSError {
        public TimeoutError() { super(); }
        public TimeoutError(String message) { super(message); }
    }

    public static class StopIteration extends RuntimeException {
        public StopIteration() { super(); }
        public StopIteration(String message) { super(message); }
    }

    public static class ImportError extends RuntimeException {
        public ImportError() { super(); }
        public ImportError(String message) { super(message); }
    }

    public static class EOFError extends RuntimeException {
        public EOFError() { super(); }
        public EOFError(String message) { super(message); }
    }

    public static class MemoryError extends RuntimeException {
        public MemoryError() { super(); }
        public MemoryError(String message) { super(message); }
    }

    public static class UnicodeError extends IllegalArgumentException {
        public UnicodeError() { super(); }
        public UnicodeError(String message) { super(message); }
    }

    /** Attach a cause, as `raise X from cause` does, and return the exception. */
    public static <T extends Throwable> T withCause(T error, Throwable cause) {
        error.initCause(cause);
        return error;
    }

    /** Thrown where CodeWeaver could not translate a construct. */
    public static <T> T unsupported(String what) {
        throw new UnsupportedOperationException("CodeWeaver could not translate: " + what);
    }

    // ---------------------------------------------------------------- tuples

    /** Python's ordering of tuple items: element by element, then by length. */
    @SuppressWarnings({"unchecked", "rawtypes"})
    static int compareItems(Object a, Object b) {
        if (a instanceof Number x && b instanceof Number y && !(a instanceof Double) && !(b instanceof Double)) {
            return Long.compare(x.longValue(), y.longValue());
        }
        if (a instanceof Number x && b instanceof Number y) {
            return Double.compare(x.doubleValue(), y.doubleValue());
        }
        if (a instanceof Comparable c) return c.compareTo(b);
        throw new TypeError("'<' not supported between instances of these types");
    }

    public record Tuple2<A, B>(A _0, B _1) implements Comparable<Tuple2<A, B>> {
        @Override public String toString() { return "(" + repr(_0) + ", " + repr(_1) + ")"; }
        @Override public int compareTo(Tuple2<A, B> o) {
            int c = compareItems(_0, o._0);
            return c != 0 ? c : compareItems(_1, o._1);
        }
    }

    public record Tuple3<A, B, C>(A _0, B _1, C _2) implements Comparable<Tuple3<A, B, C>> {
        @Override public String toString() { return "(" + repr(_0) + ", " + repr(_1) + ", " + repr(_2) + ")"; }
        @Override public int compareTo(Tuple3<A, B, C> o) {
            int c = compareItems(_0, o._0);
            if (c == 0) c = compareItems(_1, o._1);
            return c != 0 ? c : compareItems(_2, o._2);
        }
    }

    public record Tuple4<A, B, C, D>(A _0, B _1, C _2, D _3) implements Comparable<Tuple4<A, B, C, D>> {
        @Override public String toString() {
            return "(" + repr(_0) + ", " + repr(_1) + ", " + repr(_2) + ", " + repr(_3) + ")";
        }
        @Override public int compareTo(Tuple4<A, B, C, D> o) {
            int c = compareItems(_0, o._0);
            if (c == 0) c = compareItems(_1, o._1);
            if (c == 0) c = compareItems(_2, o._2);
            return c != 0 ? c : compareItems(_3, o._3);
        }
    }

    // ------------------------------------------------------------- str/repr

    /** Python's float repr: shortest round-trip, fixed notation in [1e-4, 1e16). */
    public static String str(double value) {
        if (Double.isNaN(value)) return "nan";
        if (Double.isInfinite(value)) return value > 0 ? "inf" : "-inf";
        if (value == 0) return (1 / value < 0) ? "-0.0" : "0.0";
        String text = Double.toString(value);
        java.math.BigDecimal exact = new java.math.BigDecimal(text);
        int exponent = exact.precision() - exact.scale() - 1;
        if (exponent >= -4 && exponent < 16) {
            String plain = exact.toPlainString();
            return plain.contains(".") ? plain : plain + ".0";
        }
        String digits = exact.unscaledValue().abs().toString().replaceAll("0+$", "");
        if (digits.isEmpty()) digits = "0";
        StringBuilder out = new StringBuilder();
        if (value < 0) out.append('-');
        out.append(digits.charAt(0));
        if (digits.length() > 1) out.append('.').append(digits, 1, digits.length());
        out.append('e').append(exponent < 0 ? '-' : '+');
        int magnitude = Math.abs(exponent);
        if (magnitude < 10) out.append('0');
        out.append(magnitude);
        return out.toString();
    }

    public static String str(long value) { return Long.toString(value); }

    public static String str(boolean value) { return value ? "True" : "False"; }

    public static String str(String value) { return value; }

    public static String str(Object value) {
        if (value == null) return "None";
        if (value instanceof String text) return text;
        if (value instanceof Boolean flag) return str(flag.booleanValue());
        if (value instanceof Double number) return str(number.doubleValue());
        if (value instanceof Float number) return str(number.doubleValue());
        if (value instanceof Throwable error) return error.getMessage() == null ? "" : error.getMessage();
        if (value instanceof List<?> items) {
            StringBuilder out = new StringBuilder("[");
            boolean first = true;
            for (Object item : items) {
                if (!first) out.append(", ");
                first = false;
                out.append(repr(item));
            }
            return out.append(']').toString();
        }
        if (value instanceof Set<?> items) {
            if (items.isEmpty()) return "set()";
            StringBuilder out = new StringBuilder("{");
            boolean first = true;
            for (Object item : items) {
                if (!first) out.append(", ");
                first = false;
                out.append(repr(item));
            }
            return out.append('}').toString();
        }
        if (value instanceof Map<?, ?> entries) {
            StringBuilder out = new StringBuilder("{");
            boolean first = true;
            for (Map.Entry<?, ?> entry : entries.entrySet()) {
                if (!first) out.append(", ");
                first = false;
                out.append(repr(entry.getKey())).append(": ").append(repr(entry.getValue()));
            }
            return out.append('}').toString();
        }
        return value.toString();
    }

    public static String repr(Object value) {
        if (value instanceof String text) {
            StringBuilder out = new StringBuilder("'");
            for (int i = 0; i < text.length(); i++) {
                char c = text.charAt(i);
                switch (c) {
                    case '\\' -> out.append("\\\\");
                    case '\'' -> out.append("\\'");
                    case '\n' -> out.append("\\n");
                    case '\t' -> out.append("\\t");
                    case '\r' -> out.append("\\r");
                    default -> out.append(c);
                }
            }
            return out.append('\'').toString();
        }
        return str(value);
    }

    public static void print(Object... values) {
        StringBuilder line = new StringBuilder();
        for (int i = 0; i < values.length; i++) {
            if (i > 0) line.append(' ');
            line.append(str(values[i]));
        }
        System.out.println(line);
    }

    // ----------------------------------------------------------- truthiness

    public static boolean truthy(Object value) {
        if (value == null) return false;
        if (value instanceof Boolean flag) return flag;
        if (value instanceof Long number) return number != 0;
        if (value instanceof Integer number) return number != 0;
        if (value instanceof Double number) return number != 0.0;
        if (value instanceof String text) return !text.isEmpty();
        if (value instanceof Collection<?> items) return !items.isEmpty();
        if (value instanceof Map<?, ?> entries) return !entries.isEmpty();
        return true;
    }

    public static long len(Object value) {
        if (value instanceof String text) return text.length();
        if (value instanceof Collection<?> items) return items.size();
        if (value instanceof Map<?, ?> entries) return entries.size();
        throw new TypeError("object has no len()");
    }

    // ----------------------------------------------------------- arithmetic

    /** Python's true division: always a float, and ZeroDivisionError on zero. */
    public static double div(double a, double b) {
        if (b == 0) throw new ArithmeticException("division by zero");
        return a / b;
    }

    public static double floorDiv(double a, double b) {
        if (b == 0) throw new ArithmeticException("float floor division by zero");
        return Math.floor(a / b);
    }

    public static double mod(double a, double b) {
        if (b == 0) throw new ArithmeticException("float modulo");
        double r = a % b;
        return (r != 0 && ((r < 0) != (b < 0))) ? r + b : r;
    }

    public static long pow(long base, long exponent) {
        if (exponent < 0) return (long) Math.pow(base, exponent);
        long result = 1;
        long factor = base;
        while (exponent > 0) {
            if ((exponent & 1) == 1) result = Math.multiplyExact(result, factor);
            exponent >>= 1;
            if (exponent > 0) factor = Math.multiplyExact(factor, factor);
        }
        return result;
    }

    // ------------------------------------------------------------- format

    /**
     * format(value, spec): Python's format-specification mini-language,
     * [[fill]align][sign][#][0][width][,|_][.precision][type], for int, float,
     * bool and str. f"{x:>8.2f}" is translated to format(x, ">8.2f").
     */
    public static String format(Object value, String spec) {
        if (spec.isEmpty()) return str(value);
        FormatSpec parsed = FormatSpec.parse(spec);
        if (value instanceof Boolean b) return formatInt(b ? 1 : 0, parsed);
        if (value instanceof Double || value instanceof Float) return formatFloat(((Number) value).doubleValue(), parsed);
        if (value instanceof Number n) return formatInt(n.longValue(), parsed);
        return formatStr(str(value), parsed);
    }

    public static String format(Object value) { return str(value); }

    static final class FormatSpec {
        String fill = " ";
        char align = 0, sign = '-', group = 0, type = 0;
        boolean alt = false, zero = false;
        int width = -1, precision = -1;

        static boolean isAlign(int c) { return c == '<' || c == '>' || c == '=' || c == '^'; }

        static FormatSpec parse(String spec) {
            FormatSpec out = new FormatSpec();
            int i = 0;
            int fillLen = spec.isEmpty() ? 0 : Character.charCount(spec.codePointAt(0));
            if (fillLen < spec.length() && isAlign(spec.charAt(fillLen))) {
                out.fill = spec.substring(0, fillLen);
                out.align = spec.charAt(fillLen);
                i = fillLen + 1;
            } else if (!spec.isEmpty() && isAlign(spec.charAt(0))) {
                out.align = spec.charAt(0);
                i = 1;
            }
            if (i < spec.length() && "+- ".indexOf(spec.charAt(i)) >= 0) out.sign = spec.charAt(i++);
            if (i < spec.length() && spec.charAt(i) == '#') { out.alt = true; i++; }
            if (i < spec.length() && spec.charAt(i) == '0') { out.zero = true; i++; }
            int start = i;
            while (i < spec.length() && Character.isDigit(spec.charAt(i))) i++;
            if (i > start) out.width = Integer.parseInt(spec.substring(start, i));
            if (i < spec.length() && (spec.charAt(i) == ',' || spec.charAt(i) == '_')) out.group = spec.charAt(i++);
            if (i < spec.length() && spec.charAt(i) == '.') {
                start = ++i;
                while (i < spec.length() && Character.isDigit(spec.charAt(i))) i++;
                if (i == start) throw new IllegalArgumentException("Format specifier missing precision");
                out.precision = Integer.parseInt(spec.substring(start, i));
            }
            if (i < spec.length()) out.type = spec.charAt(i++);
            if (i != spec.length()) throw new IllegalArgumentException("Invalid format specifier '" + spec + "'");
            return out;
        }
    }

    static String groupDigits(String digits, char sep, int every) {
        StringBuilder out = new StringBuilder();
        int lead = digits.length() % every;
        for (int i = 0; i < digits.length(); i++) {
            if (i > 0 && i >= lead && (i - lead) % every == 0) out.append(sep);
            out.append(digits.charAt(i));
        }
        return out.toString();
    }

    static String pad(String prefix, String body, FormatSpec spec, boolean numeric) {
        String fill = spec.fill;
        char align = spec.align;
        if (spec.zero && align == 0) { fill = "0"; align = '='; }
        if (align == 0) align = numeric ? '>' : '<';
        int length = prefix.codePointCount(0, prefix.length()) + body.codePointCount(0, body.length());
        if (spec.width <= length) return prefix + body;
        int n = spec.width - length;
        switch (align) {
            case '<': return prefix + body + fill.repeat(n);
            case '^': return fill.repeat(n / 2) + prefix + body + fill.repeat(n - n / 2);
            case '=': return prefix + fill.repeat(n) + body;
            default: return fill.repeat(n) + prefix + body;
        }
    }

    static String signOf(boolean negative, FormatSpec spec) {
        if (negative) return "-";
        if (spec.sign == '+') return "+";
        if (spec.sign == ' ') return " ";
        return "";
    }

    static String formatInt(long value, FormatSpec spec) {
        char type = spec.type;
        if ("eEfFgG%".indexOf(type) >= 0 && type != 0) return formatFloat((double) value, spec);
        if (spec.precision >= 0) throw new IllegalArgumentException("Precision not allowed in integer format specifier");
        boolean negative = value < 0;
        java.math.BigInteger magnitude = java.math.BigInteger.valueOf(value).abs();
        String prefix = signOf(negative, spec);
        int base;
        switch (type) {
            case 'x': case 'X': base = 16; break;
            case 'o': base = 8; break;
            case 'b': base = 2; break;
            case 0: case 'd': case 'n': base = 10; break;
            default: throw new IllegalArgumentException("Unknown format code '" + type + "' for object of type 'int'");
        }
        String digits = magnitude.toString(base);
        if (type == 'X') digits = digits.toUpperCase();
        if (spec.alt && base != 10) prefix += base == 16 ? (type == 'X' ? "0X" : "0x") : base == 8 ? "0o" : "0b";
        if (spec.group != 0) digits = groupDigits(digits, spec.group, base == 10 ? 3 : 4);
        return pad(prefix, digits, spec, true);
    }

    /** printf-style fixed/exponent formatting from the exact binary value, rounded half to even. */
    static String fixed(double magnitude, int precision) {
        return new java.math.BigDecimal(magnitude).setScale(precision, java.math.RoundingMode.HALF_EVEN).toPlainString();
    }

    static String exponent(double magnitude, int precision, boolean alt) {
        if (magnitude == 0.0) {
            return (precision > 0 ? "0." + "0".repeat(precision) : (alt ? "0." : "0")) + "e+00";
        }
        java.math.BigDecimal exact = new java.math.BigDecimal(magnitude);
        java.math.BigDecimal rounded = exact.round(new java.math.MathContext(precision + 1, java.math.RoundingMode.HALF_EVEN));
        String digits = rounded.unscaledValue().toString();
        int exp = digits.length() - 1 - rounded.scale();
        digits = (digits + "0".repeat(precision + 1)).substring(0, precision + 1);
        String mantissa = digits.substring(0, 1) + (precision > 0 ? "." + digits.substring(1) : (alt ? "." : ""));
        return mantissa + (exp < 0 ? "e-" : "e+") + (Math.abs(exp) < 10 ? "0" : "") + Math.abs(exp);
    }

    /** C's %g: precision significant digits, trailing zeros removed unless alt. */
    static String general(double magnitude, int precision, boolean alt) {
        if (precision == 0) precision = 1;
        int exp;
        if (magnitude == 0.0) {
            exp = 0;
        } else {
            String e = exponent(magnitude, precision - 1, false);
            exp = Integer.parseInt(e.substring(e.indexOf('e') + 1));
        }
        String body;
        if (exp >= -4 && exp < precision) {
            body = fixed(magnitude, precision - 1 - exp);
            if (alt && body.indexOf('.') < 0) body += ".";
        } else {
            body = exponent(magnitude, precision - 1, alt);
        }
        if (!alt) {
            int e = body.indexOf('e');
            String mantissa = e < 0 ? body : body.substring(0, e);
            String tail = e < 0 ? "" : body.substring(e);
            if (mantissa.indexOf('.') >= 0) {
                mantissa = mantissa.replaceAll("0+$", "");
                if (mantissa.endsWith(".")) mantissa = mantissa.substring(0, mantissa.length() - 1);
            }
            body = mantissa + tail;
        }
        return body;
    }

    static String formatFloat(double value, FormatSpec spec) {
        char type = spec.type;
        boolean negative = (Double.doubleToRawLongBits(value) & Long.MIN_VALUE) != 0;
        double magnitude = Math.abs(value);
        boolean upper = type == 'F' || type == 'E' || type == 'G';
        String body;
        if (Double.isNaN(value) || Double.isInfinite(value)) {
            body = Double.isNaN(value) ? "nan" : "inf";
            if (Double.isNaN(value)) negative = false;
            if (type == '%') body += "%";
        } else if (type == 'f' || type == 'F') {
            body = fixed(magnitude, spec.precision < 0 ? 6 : spec.precision);
            if (spec.alt && body.indexOf('.') < 0) body += ".";
        } else if (type == 'e' || type == 'E') {
            body = exponent(magnitude, spec.precision < 0 ? 6 : spec.precision, spec.alt);
        } else if (type == 'g' || type == 'G') {
            body = general(magnitude, spec.precision < 0 ? 6 : spec.precision, spec.alt);
        } else if (type == '%') {
            body = fixed(magnitude * 100.0, spec.precision < 0 ? 6 : spec.precision) + "%";
        } else if (type == 0 || type == 'n') {
            if (spec.precision < 0) {
                body = str(magnitude);
            } else {
                body = general(magnitude, spec.precision, false);
                if (body.indexOf('.') < 0 && body.indexOf('e') < 0) body += ".0";
            }
        } else {
            throw new IllegalArgumentException("Unknown format code '" + type + "' for object of type 'float'");
        }
        if (upper) body = body.toUpperCase();
        if (spec.group != 0) {
            int end = 0;
            while (end < body.length() && Character.isDigit(body.charAt(end))) end++;
            body = groupDigits(body.substring(0, end), spec.group, 3) + body.substring(end);
        }
        return pad(signOf(negative, spec), body, spec, true);
    }

    static String formatStr(String value, FormatSpec spec) {
        if (spec.type != 0 && spec.type != 's') {
            throw new IllegalArgumentException("Unknown format code '" + spec.type + "' for object of type 'str'");
        }
        if (spec.sign != '-') throw new IllegalArgumentException("Sign not allowed in string format specifier");
        if (spec.align == '=') throw new IllegalArgumentException("'=' alignment not allowed in string format specifier");
        String body = value;
        if (spec.precision >= 0 && body.codePointCount(0, body.length()) > spec.precision) {
            body = body.substring(0, body.offsetByCodePoints(0, spec.precision));
        }
        return pad("", body, spec, false);
    }

    /** round(x, n): halves go to the even neighbour, as in Python. */
    public static double round(double value, long digits) {
        if (Double.isNaN(value) || Double.isInfinite(value) || digits > 323) return value;
        // The exact binary value, as Python rounds it: round(2.675, 2) is 2.67
        // because 2.675 is stored as 2.67499999...
        java.math.BigDecimal exact = new java.math.BigDecimal(value);
        double rounded = exact.setScale((int) digits, java.math.RoundingMode.HALF_EVEN).doubleValue();
        return rounded == 0.0 && (value < 0 || 1 / value < 0) ? -0.0 : rounded;
    }

    public static long toInt(String text) {
        try {
            return Long.parseLong(text.strip().replace("_", ""));
        } catch (NumberFormatException error) {
            throw new IllegalArgumentException("invalid literal for int() with base 10: " + repr(text));
        }
    }

    public static double toFloat(String text) {
        try {
            return Double.parseDouble(text.strip());
        } catch (NumberFormatException error) {
            throw new IllegalArgumentException("could not convert string to float: " + repr(text));
        }
    }

    // ------------------------------------------------------------ sequences

    private static int position(long index, int size) { return position(index, size, "list"); }

    private static int position(long index, int size, String what) {
        long at = index < 0 ? index + size : index;
        if (at < 0 || at >= size) throw new IndexOutOfBoundsException(what + " index out of range");
        return (int) at;
    }

    public static <T> T get(List<T> items, long index) { return items.get(position(index, items.size())); }

    public static <T> T setItem(List<T> items, long index, T value) {
        items.set(position(index, items.size()), value);
        return value;
    }

    public static String charAt(String text, long index) {
        return String.valueOf(text.charAt(position(index, text.length(), "string")));
    }

    public static <K, V> V getKey(Map<K, V> entries, K key) {
        V found = entries.get(key);
        if (found == null && !entries.containsKey(key)) throw new NoSuchElementException(repr(key));
        return found;
    }

    private static int bound(Long value, int size, int fallback) {
        if (value == null) return fallback;
        long at = value < 0 ? value + size : value;
        return (int) Math.max(0, Math.min(at, size));
    }

    public static <T> List<T> slice(List<T> items, Long lower, Long upper, Long step) {
        long stride = step == null ? 1 : step;
        if (stride == 0) throw new IllegalArgumentException("slice step cannot be zero");
        int size = items.size();
        List<T> out = new ArrayList<>();
        if (stride > 0) {
            int first = bound(lower, size, 0);
            int last = bound(upper, size, size);
            if (stride == 1) return first < last ? new ArrayList<>(items.subList(first, last)) : out;
            for (int i = first; i < last; i += (int) stride) out.add(items.get(i));
            return out;
        }
        long first = lower == null ? size - 1 : (lower < 0 ? lower + size : lower);
        long last = upper == null ? -1 : (upper < 0 ? upper + size : upper);
        first = Math.min(first, size - 1);
        last = Math.max(last, -1);
        for (long i = first; i > last; i += stride) out.add(items.get((int) i));
        return out;
    }

    public static String slice(String text, Long lower, Long upper, Long step) {
        List<String> chars = chars(text);
        StringBuilder out = new StringBuilder();
        for (String part : slice(chars, lower, upper, step)) out.append(part);
        return out.toString();
    }

    public static List<String> chars(String text) {
        List<String> out = new ArrayList<>(text.length());
        for (int i = 0; i < text.length(); i++) out.add(String.valueOf(text.charAt(i)));
        return out;
    }

    public static <T> List<T> concat(List<? extends T> a, List<? extends T> b) {
        List<T> out = new ArrayList<>(a.size() + b.size());
        out.addAll(a);
        out.addAll(b);
        return out;
    }

    public static <T> List<T> repeat(List<T> items, long count) {
        List<T> out = new ArrayList<>();
        for (long i = 0; i < count; i++) out.addAll(items);
        return out;
    }

    public static List<Long> range(long stop) { return range(0, stop, 1); }

    public static List<Long> range(long start, long stop) { return range(start, stop, 1); }

    public static List<Long> range(long start, long stop, long step) {
        if (step == 0) throw new IllegalArgumentException("range() arg 3 must not be zero");
        List<Long> out = new ArrayList<>();
        for (long i = start; step > 0 ? i < stop : i > stop; i += step) out.add(i);
        return out;
    }

    // ------------------------------------------------------- list methods

    public static <T> T pop(List<T> items) {
        if (items.isEmpty()) throw new IndexOutOfBoundsException("pop from empty list");
        return items.remove(items.size() - 1);
    }

    public static <T> T pop(List<T> items, long index) {
        if (items.isEmpty()) throw new IndexOutOfBoundsException("pop from empty list");
        return items.remove(position(index, items.size()));
    }

    public static <T> void insert(List<T> items, long index, T value) {
        int size = items.size();
        long at = index < 0 ? Math.max(index + size, 0) : Math.min(index, size);
        items.add((int) at, value);
    }

    public static <T> void remove(List<T> items, Object value) {
        if (!items.remove(value)) throw new IllegalArgumentException("list.remove(x): x not in list");
    }

    public static <T> void remove(Set<T> items, Object value) {
        if (!items.remove(value)) throw new NoSuchElementException(repr(value));
    }

    public static long index(List<?> items, Object value) {
        int at = items.indexOf(value);
        if (at < 0) throw new IllegalArgumentException(repr(value) + " is not in list");
        return at;
    }

    public static long index(String text, String needle) {
        int at = text.indexOf(needle);
        if (at < 0) throw new IllegalArgumentException("substring not found");
        return at;
    }

    public static long count(List<?> items, Object value) { return Collections.frequency(items, value); }

    public static long count(String text, String needle) {
        if (needle.isEmpty()) return text.length() + 1L;
        long total = 0;
        for (int at = text.indexOf(needle); at >= 0; at = text.indexOf(needle, at + needle.length())) total++;
        return total;
    }

    public static <T extends Comparable<? super T>> void sort(List<T> items, boolean reverse) {
        items.sort(reverse ? Comparator.reverseOrder() : Comparator.naturalOrder());
    }

    public static <T, K extends Comparable<? super K>> void sortBy(List<T> items, Function<? super T, ? extends K> key, boolean reverse) {
        Comparator<T> order = Comparator.comparing(key);
        items.sort(reverse ? order.reversed() : order);
    }

    public static <T extends Comparable<? super T>> List<T> sorted(Collection<T> items, boolean reverse) {
        List<T> out = new ArrayList<>(items);
        sort(out, reverse);
        return out;
    }

    public static <T, K extends Comparable<? super K>> List<T> sortedBy(Collection<T> items, Function<? super T, ? extends K> key, boolean reverse) {
        List<T> out = new ArrayList<>(items);
        sortBy(out, key, reverse);
        return out;
    }

    public static long sumLong(Iterable<? extends Number> items) {
        long total = 0;
        for (Number item : items) total += item.longValue();
        return total;
    }

    public static double sumDouble(Iterable<? extends Number> items) {
        double total = 0;
        for (Number item : items) total += item.doubleValue();
        return total;
    }

    public static <T extends Comparable<? super T>> T min(Collection<T> items) {
        if (items.isEmpty()) throw new IllegalArgumentException("min() arg is an empty sequence");
        return Collections.min(items);
    }

    public static <T extends Comparable<? super T>> T max(Collection<T> items) {
        if (items.isEmpty()) throw new IllegalArgumentException("max() arg is an empty sequence");
        return Collections.max(items);
    }

    public static boolean any(Iterable<?> items) {
        for (Object item : items) if (truthy(item)) return true;
        return false;
    }

    public static boolean all(Iterable<?> items) {
        for (Object item : items) if (!truthy(item)) return false;
        return true;
    }

    public static boolean contains(Object container, Object item) {
        if (container instanceof String text) return text.contains((String) item);
        if (container instanceof Map<?, ?> entries) return entries.containsKey(item);
        if (container instanceof Collection<?> items) return items.contains(item);
        throw new TypeError("argument is not iterable");
    }

    // ------------------------------------------------------- dict methods

    /** zip(a, b): pairs of the items at each position, stopping at the shortest. */
    public static <A, B> List<Tuple2<A, B>> zip(List<A> a, List<B> b) {
        int n = Math.min(a.size(), b.size());
        List<Tuple2<A, B>> out = new ArrayList<>(n);
        for (int i = 0; i < n; i++) out.add(new Tuple2<>(a.get(i), b.get(i)));
        return out;
    }

    public static <A, B, C> List<Tuple3<A, B, C>> zip(List<A> a, List<B> b, List<C> c) {
        int n = Math.min(a.size(), Math.min(b.size(), c.size()));
        List<Tuple3<A, B, C>> out = new ArrayList<>(n);
        for (int i = 0; i < n; i++) out.add(new Tuple3<>(a.get(i), b.get(i), c.get(i)));
        return out;
    }

    public static List<Tuple2<String, String>> zip(String a, String b) {
        return zip(chars(a), chars(b));
    }

    public static <K, V> List<Tuple2<K, V>> items(Map<K, V> entries) {
        List<Tuple2<K, V>> out = new ArrayList<>(entries.size());
        for (Map.Entry<K, V> entry : entries.entrySet()) out.add(new Tuple2<>(entry.getKey(), entry.getValue()));
        return out;
    }

    public static <K, V> V pop(Map<K, V> entries, K key) {
        if (!entries.containsKey(key)) throw new NoSuchElementException(repr(key));
        return entries.remove(key);
    }

    public static <K, V> V setdefault(Map<K, V> entries, K key, V fallback) {
        if (!entries.containsKey(key)) entries.put(key, fallback);
        return entries.get(key);
    }

    // -------------------------------------------------------- set methods

    public static <T> Set<T> union(Set<T> a, Set<T> b) {
        Set<T> out = new LinkedHashSet<>(a);
        out.addAll(b);
        return out;
    }

    public static <T> Set<T> intersection(Set<T> a, Set<T> b) {
        Set<T> out = new LinkedHashSet<>(a);
        out.retainAll(b);
        return out;
    }

    public static <T> Set<T> difference(Set<T> a, Set<T> b) {
        Set<T> out = new LinkedHashSet<>(a);
        out.removeAll(b);
        return out;
    }

    // -------------------------------------------------------- str methods

    public static List<String> split(String text) {
        List<String> out = new ArrayList<>();
        for (String part : text.strip().split("\\s+")) if (!part.isEmpty()) out.add(part);
        return out;
    }

    public static List<String> split(String text, String separator) {
        if (separator.isEmpty()) throw new IllegalArgumentException("empty separator");
        List<String> out = new ArrayList<>();
        int start = 0;
        for (int at = text.indexOf(separator); at >= 0; at = text.indexOf(separator, start)) {
            out.add(text.substring(start, at));
            start = at + separator.length();
        }
        out.add(text.substring(start));
        return out;
    }

    private static boolean allChars(String text, java.util.function.IntPredicate test) {
        return !text.isEmpty() && text.chars().allMatch(test);
    }

    public static boolean isdigit(String text) { return allChars(text, Character::isDigit); }

    public static boolean isalpha(String text) { return allChars(text, Character::isLetter); }

    public static boolean isalnum(String text) { return allChars(text, Character::isLetterOrDigit); }

    public static boolean isspace(String text) { return allChars(text, Character::isWhitespace); }

    public static boolean isupper(String text) {
        return text.chars().anyMatch(Character::isUpperCase) && text.chars().noneMatch(Character::isLowerCase);
    }

    public static boolean islower(String text) {
        return text.chars().anyMatch(Character::isLowerCase) && text.chars().noneMatch(Character::isUpperCase);
    }

    public static String capitalize(String text) {
        if (text.isEmpty()) return text;
        return text.substring(0, 1).toUpperCase() + text.substring(1).toLowerCase();
    }

    public static String title(String text) {
        StringBuilder out = new StringBuilder(text.length());
        boolean start = true;
        for (int i = 0; i < text.length(); i++) {
            char c = text.charAt(i);
            out.append(start ? Character.toUpperCase(c) : Character.toLowerCase(c));
            start = !Character.isLetter(c);
        }
        return out.toString();
    }

    public static String zfill(String text, long width) {
        if (text.length() >= width) return text;
        String pad = "0".repeat((int) width - text.length());
        if (!text.isEmpty() && (text.charAt(0) == '-' || text.charAt(0) == '+')) {
            return text.charAt(0) + pad + text.substring(1);
        }
        return pad + text;
    }

    /** Run a program entry point, printing an uncaught exception as Python does. */
    public static void runMain(Runnable body) {
        try {
            body.run();
        } catch (RuntimeException | AssertionError | StackOverflowError error) {
            System.out.flush();
            System.err.println("Traceback (most recent call last):");
            System.err.println(pythonName(error) + ": " + pythonMessage(error));
            System.exit(1);
        }
    }

    /** The Python exception a Java runtime exception stands for. */
    static String pythonName(Throwable error) {
        if (error instanceof ArithmeticException) {
            return String.valueOf(error.getMessage()).contains("overflow") ? "OverflowError" : "ZeroDivisionError";
        }
        if (error instanceof IndexOutOfBoundsException) return "IndexError";
        if (error instanceof NoSuchElementException) return "KeyError";
        if (error instanceof ClassCastException) return "TypeError";
        if (error instanceof NullPointerException) return "AttributeError";
        if (error instanceof StackOverflowError) return "RecursionError";
        if (error instanceof UnsupportedOperationException) return "NotImplementedError";
        if (error instanceof AssertionError) return "AssertionError";
        if (error.getClass() == IllegalArgumentException.class || error instanceof NumberFormatException) return "ValueError";
        return error.getClass().getSimpleName();
    }

    static String pythonMessage(Throwable error) {
        if (error instanceof ArithmeticException) {
            return String.valueOf(error.getMessage()).contains("overflow")
                ? "integer result does not fit in 64 bits (Python ints are unbounded)"
                : "integer division or modulo by zero";
        }
        if (error instanceof StackOverflowError) return "maximum recursion depth exceeded";
        if (error instanceof NullPointerException) return "'NoneType' object has no attribute";
        return str(error);
    }

    // ---------------------------------------------------------- int arithmetic
    // Python's int never overflows; long does, and wrapping silently would
    // print a wrong answer. These throw instead (an intrinsic overflow check).
    // The long-only signatures stop javac picking Math.addExact(int, int).

    public static long add(long a, long b) { return Math.addExact(a, b); }
    public static long sub(long a, long b) { return Math.subtractExact(a, b); }
    public static long mul(long a, long b) { return Math.multiplyExact(a, b); }

    // ------------------------------------------------------------ factories
    // Mutable, insertion-ordered collections, accepting null elements.

    @SafeVarargs
    public static <T> ArrayList<T> list(T... items) {
        ArrayList<T> out = new ArrayList<>(Math.max(items.length, 4));
        Collections.addAll(out, items);
        return out;
    }

    @SafeVarargs
    public static <T> LinkedHashSet<T> set(T... items) {
        LinkedHashSet<T> out = new LinkedHashSet<>();
        Collections.addAll(out, items);
        return out;
    }

    public static <K, V> Map.Entry<K, V> entry(K key, V value) {
        return new java.util.AbstractMap.SimpleImmutableEntry<>(key, value);
    }

    @SafeVarargs
    public static <K, V> LinkedHashMap<K, V> dict(Map.Entry<? extends K, ? extends V>... entries) {
        LinkedHashMap<K, V> out = new LinkedHashMap<>();
        for (Map.Entry<? extends K, ? extends V> entry : entries) out.put(entry.getKey(), entry.getValue());
        return out;
    }

    // ------------------------------------------------------- comprehensions
    // Plain loops: no stream pipeline, no intermediate allocation.

    public static <T, R> ArrayList<R> listComp(Iterable<T> items, Function<? super T, ? extends R> element) {
        ArrayList<R> out = new ArrayList<>();
        for (T item : items) out.add(element.apply(item));
        return out;
    }

    public static <T, R> ArrayList<R> listCompIf(
            Iterable<T> items, java.util.function.Predicate<? super T> keep, Function<? super T, ? extends R> element) {
        ArrayList<R> out = new ArrayList<>();
        for (T item : items) if (keep.test(item)) out.add(element.apply(item));
        return out;
    }

    public static <T, R> LinkedHashSet<R> setComp(Iterable<T> items, Function<? super T, ? extends R> element) {
        LinkedHashSet<R> out = new LinkedHashSet<>();
        for (T item : items) out.add(element.apply(item));
        return out;
    }

    public static <T, R> LinkedHashSet<R> setCompIf(
            Iterable<T> items, java.util.function.Predicate<? super T> keep, Function<? super T, ? extends R> element) {
        LinkedHashSet<R> out = new LinkedHashSet<>();
        for (T item : items) if (keep.test(item)) out.add(element.apply(item));
        return out;
    }

    public static <T, K, V> LinkedHashMap<K, V> dictComp(
            Iterable<T> items, Function<? super T, ? extends K> key, Function<? super T, ? extends V> value) {
        LinkedHashMap<K, V> out = new LinkedHashMap<>();
        for (T item : items) out.put(key.apply(item), value.apply(item));
        return out;
    }

    public static <T, K, V> LinkedHashMap<K, V> dictCompIf(
            Iterable<T> items,
            java.util.function.Predicate<? super T> keep,
            Function<? super T, ? extends K> key,
            Function<? super T, ? extends V> value) {
        LinkedHashMap<K, V> out = new LinkedHashMap<>();
        for (T item : items) if (keep.test(item)) out.put(key.apply(item), value.apply(item));
        return out;
    }

    public static long gcd(long a, long b) {
        a = Math.abs(a);
        b = Math.abs(b);
        while (b != 0) {
            long t = a % b;
            a = b;
            b = t;
        }
        return a;
    }
}
