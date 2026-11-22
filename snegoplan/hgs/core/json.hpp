// Minimal self-contained JSON reader (UTF-8 aware). Numeric arrays are stored compactly.
#pragma once
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace json {

struct Value {
    enum Type { Null, Bool, Number, String, Array, Object };
    Type type = Null;
    bool b = false;
    double num = 0;
    std::string str;
    bool numeric = false;          // Array stored in `nums`
    std::vector<double> nums;
    std::vector<Value> arr;        // Array (non-numeric) elements
    std::vector<std::string> keys; // Object
    std::vector<Value> vals;

    bool isNull() const { return type == Null; }
    bool isNumber() const { return type == Number; }
    bool isString() const { return type == String; }
    bool isArray() const { return type == Array; }
    bool isObject() const { return type == Object; }
    size_t size() const {
        if (type == Array) return numeric ? nums.size() : arr.size();
        if (type == Object) return keys.size();
        return 0;
    }
    const Value* find(const std::string& k) const {
        if (type != Object) return nullptr;
        for (size_t i = 0; i < keys.size(); ++i)
            if (keys[i] == k) return &vals[i];
        return nullptr;
    }
    // Typed getters with defaults (null / missing -> default)
    double getNum(const std::string& k, double def) const {
        const Value* v = find(k);
        if (!v || v->type != Number) return def;
        return v->num;
    }
    std::string getStr(const std::string& k, const std::string& def) const {
        const Value* v = find(k);
        if (!v || v->type != String) return def;
        return v->str;
    }
    bool getBool(const std::string& k, bool def) const {
        const Value* v = find(k);
        if (!v || v->type != Bool) return def;
        return v->b;
    }
    // Array element as Value (materialises numbers on demand; use numAt for speed)
    double numAt(size_t i) const {
        if (numeric) return nums[i];
        return arr[i].num;
    }
    const Value& at(size_t i) const { return arr[i]; }
};

class Parser {
public:
    explicit Parser(const std::string& s) : p_(s.data()), e_(s.data() + s.size()) {
        if (e_ - p_ >= 3 && (unsigned char)p_[0] == 0xEF && (unsigned char)p_[1] == 0xBB &&
            (unsigned char)p_[2] == 0xBF)
            p_ += 3;
    }
    Value parse() {
        Value v;
        parseValue(v, 0);
        ws();
        if (p_ != e_) fail("trailing characters");
        return v;
    }

private:
    const char* p_;
    const char* e_;

    [[noreturn]] void fail(const char* m) { throw std::runtime_error(std::string("JSON parse error: ") + m); }
    void ws() {
        while (p_ < e_ && (*p_ == ' ' || *p_ == '\t' || *p_ == '\n' || *p_ == '\r')) ++p_;
    }
    bool lit(const char* s) {
        size_t n = std::strlen(s);
        if ((size_t)(e_ - p_) >= n && std::memcmp(p_, s, n) == 0) {
            p_ += n;
            return true;
        }
        return false;
    }
    void parseNumber(Value& v) {
        const char* s = p_;
        if (p_ < e_ && (*p_ == '-' || *p_ == '+')) ++p_;
        while (p_ < e_ && ((*p_ >= '0' && *p_ <= '9') || *p_ == '.' || *p_ == 'e' || *p_ == 'E' || *p_ == '-' || *p_ == '+')) ++p_;
        if (p_ == s) fail("bad number");
        std::string tmp(s, p_);
        char* endp = nullptr;
        v.type = Value::Number;
        v.num = std::strtod(tmp.c_str(), &endp);
        if (endp == tmp.c_str()) fail("bad number");
    }
    static void putUtf8(std::string& o, unsigned cp) {
        if (cp < 0x80) o += (char)cp;
        else if (cp < 0x800) { o += (char)(0xC0 | (cp >> 6)); o += (char)(0x80 | (cp & 0x3F)); }
        else if (cp < 0x10000) { o += (char)(0xE0 | (cp >> 12)); o += (char)(0x80 | ((cp >> 6) & 0x3F)); o += (char)(0x80 | (cp & 0x3F)); }
        else { o += (char)(0xF0 | (cp >> 18)); o += (char)(0x80 | ((cp >> 12) & 0x3F)); o += (char)(0x80 | ((cp >> 6) & 0x3F)); o += (char)(0x80 | (cp & 0x3F)); }
    }
    unsigned hex4() {
        if (e_ - p_ < 4) fail("bad \\u escape");
        unsigned r = 0;
        for (int i = 0; i < 4; ++i) {
            char c = *p_++;
            r <<= 4;
            if (c >= '0' && c <= '9') r |= (unsigned)(c - '0');
            else if (c >= 'a' && c <= 'f') r |= (unsigned)(c - 'a' + 10);
            else if (c >= 'A' && c <= 'F') r |= (unsigned)(c - 'A' + 10);
            else fail("bad hex digit");
        }
        return r;
    }
    void parseString(std::string& out) {
        ++p_; // opening quote
        out.clear();
        while (true) {
            if (p_ >= e_) fail("unterminated string");
            char c = *p_++;
            if (c == '"') return;
            if (c != '\\') { out += c; continue; }
            if (p_ >= e_) fail("bad escape");
            char x = *p_++;
            switch (x) {
                case '"': out += '"'; break;
                case '\\': out += '\\'; break;
                case '/': out += '/'; break;
                case 'b': out += '\b'; break;
                case 'f': out += '\f'; break;
                case 'n': out += '\n'; break;
                case 'r': out += '\r'; break;
                case 't': out += '\t'; break;
                case 'u': {
                    unsigned cp = hex4();
                    if (cp >= 0xD800 && cp < 0xDC00 && e_ - p_ >= 6 && p_[0] == '\\' && p_[1] == 'u') {
                        p_ += 2;
                        unsigned lo = hex4();
                        if (lo >= 0xDC00 && lo < 0xE000) cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                        else fail("bad surrogate pair");
                    }
                    putUtf8(out, cp);
                    break;
                }
                default: fail("bad escape");
            }
        }
    }
    void parseValue(Value& v, int depth) {
        if (depth > 200) fail("nesting too deep");
        ws();
        if (p_ >= e_) fail("unexpected end");
        char c = *p_;
        if (c == '{') {
            ++p_;
            v.type = Value::Object;
            ws();
            if (p_ < e_ && *p_ == '}') { ++p_; return; }
            while (true) {
                ws();
                if (p_ >= e_ || *p_ != '"') fail("expected key");
                std::string k;
                parseString(k);
                ws();
                if (p_ >= e_ || *p_ != ':') fail("expected ':'");
                ++p_;
                v.keys.push_back(std::move(k));
                v.vals.emplace_back();
                parseValue(v.vals.back(), depth + 1);
                ws();
                if (p_ < e_ && *p_ == ',') { ++p_; continue; }
                if (p_ < e_ && *p_ == '}') { ++p_; return; }
                fail("expected ',' or '}'");
            }
        } else if (c == '[') {
            ++p_;
            v.type = Value::Array;
            ws();
            if (p_ < e_ && *p_ == ']') { ++p_; v.numeric = false; return; }
            bool first = true;
            while (true) {
                ws();
                if (p_ >= e_) fail("unexpected end in array");
                bool isNum = (*p_ == '-' || *p_ == '+' || (*p_ >= '0' && *p_ <= '9'));
                if (first) { v.numeric = isNum; first = false; }
                if (v.numeric && isNum) {
                    Value t;
                    parseNumber(t);
                    v.nums.push_back(t.num);
                } else {
                    if (v.numeric) { // demote to generic array
                        v.numeric = false;
                        v.arr.reserve(v.nums.size() + 1);
                        for (double d : v.nums) { v.arr.emplace_back(); v.arr.back().type = Value::Number; v.arr.back().num = d; }
                        v.nums.clear();
                    }
                    v.arr.emplace_back();
                    parseValue(v.arr.back(), depth + 1);
                }
                ws();
                if (p_ < e_ && *p_ == ',') { ++p_; continue; }
                if (p_ < e_ && *p_ == ']') { ++p_; return; }
                fail("expected ',' or ']'");
            }
        } else if (c == '"') {
            v.type = Value::String;
            parseString(v.str);
        } else if (lit("true")) { v.type = Value::Bool; v.b = true; }
        else if (lit("false")) { v.type = Value::Bool; v.b = false; }
        else if (lit("null")) { v.type = Value::Null; }
        else parseNumber(v);
    }
};

inline Value parse(const std::string& s) { return Parser(s).parse(); }

// ---- writer helpers ----
inline void escapeTo(std::string& o, const std::string& s) {
    o += '"';
    for (unsigned char c : s) {
        switch (c) {
            case '"': o += "\\\""; break;
            case '\\': o += "\\\\"; break;
            case '\n': o += "\\n"; break;
            case '\r': o += "\\r"; break;
            case '\t': o += "\\t"; break;
            default:
                if (c < 0x20) { char b[8]; std::snprintf(b, sizeof b, "\\u%04x", c); o += b; }
                else o += (char)c; // UTF-8 bytes pass through unchanged
        }
    }
    o += '"';
}
inline void numTo(std::string& o, double d) {
    if (!std::isfinite(d)) { o += "null"; return; }
    char b[40];
    if (d == std::floor(d) && std::fabs(d) < 1e15) std::snprintf(b, sizeof b, "%.0f", d);
    else std::snprintf(b, sizeof b, "%.10g", d);
    o += b;
}

} // namespace json
