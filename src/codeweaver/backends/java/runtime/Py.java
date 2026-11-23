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

    public record Tuple2<A, B>(A _0, B _1) {
        @Override public String toString() { return "(" + repr(_0) + ", " + repr(_1) + ")"; }
    }

    public record Tuple3<A, B, C>(A _0, B _1, C _2) {
        @Override public String toString() { return "(" + repr(_0) + ", " + repr(_1) + ", " + repr(_2) + ")"; }
    }

    public record Tuple4<A, B, C, D>(A _0, B _1, C _2, D _3) {
        @Override public String toString() {
            return "(" + repr(_0) + ", " + repr(_1) + ", " + repr(_2) + ", " + repr(_3) + ")";
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
            if ((exponent & 1) == 1) result *= factor;
            factor *= factor;
            exponent >>= 1;
        }
        return result;
    }

    /** round(x, n): halves go to the even neighbour, as in Python. */
    public static double round(double value, long digits) {
        java.math.BigDecimal exact = new java.math.BigDecimal(Double.toString(value));
        return exact.setScale((int) digits, java.math.RoundingMode.HALF_EVEN).doubleValue();
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

    private static int position(long index, int size) {
        long at = index < 0 ? index + size : index;
        if (at < 0 || at >= size) throw new IndexOutOfBoundsException("index out of range");
        return (int) at;
    }

    public static <T> T get(List<T> items, long index) { return items.get(position(index, items.size())); }

    public static <T> T setItem(List<T> items, long index, T value) {
        items.set(position(index, items.size()), value);
        return value;
    }

    public static String charAt(String text, long index) {
        return String.valueOf(text.charAt(position(index, text.length())));
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
        } catch (RuntimeException | AssertionError error) {
            System.out.flush();
            System.err.println("Traceback (most recent call last):");
            System.err.println(error.getClass().getSimpleName() + ": " + str(error));
            System.exit(1);
        }
    }

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
