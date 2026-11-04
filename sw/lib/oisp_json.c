// SPDX-License-Identifier: MIT
/* Minimal JSON reader (RFC 8259 subset: no \u escapes beyond ASCII). */
#include <ctype.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>

#include "oisp.h"

struct jp {
	const char *s;
	int line;
	char *err;
	size_t errlen;
	int depth;
};

static void *jfail(struct jp *p, const char *msg)
{
	if (p->err && p->errlen && !p->err[0])
		snprintf(p->err, p->errlen, "json line %d: %s", p->line, msg);
	return NULL;
}

static void ws(struct jp *p)
{
	for (;;) {
		while (isspace((unsigned char)*p->s)) {
			if (*p->s == '\n')
				p->line++;
			p->s++;
		}
		if (p->s[0] == '/' && p->s[1] == '/') {		/* comments are accepted */
			while (*p->s && *p->s != '\n')
				p->s++;
			continue;
		}
		return;
	}
}

static struct oisp_json *node(enum oisp_jtype t)
{
	struct oisp_json *j = calloc(1, sizeof(*j));

	if (j)
		j->type = t;
	return j;
}

static char *jstring(struct jp *p)
{
	size_t cap = 16, n = 0;
	char *out = malloc(cap);

	if (!out)
		return jfail(p, "out of memory");
	p->s++;			/* opening quote */
	while (*p->s != '"') {
		char c = *p->s++;

		if (!c || c == '\n') {
			free(out);
			return jfail(p, "unterminated string");
		}
		if (c == '\\') {
			c = *p->s++;
			switch (c) {
			case 'n': c = '\n'; break;
			case 't': c = '\t'; break;
			case 'r': c = '\r'; break;
			case 'b': c = '\b'; break;
			case 'f': c = '\f'; break;
			case 'u': {
				unsigned int v = 0, k;

				for (k = 0; k < 4; k++) {
					char h = *p->s++;

					v = v * 16 + (isdigit((unsigned char)h) ? h - '0' : (tolower(h) - 'a' + 10));
				}
				c = v < 128 ? (char)v : '?';
				break;
			}
			default: break;		/* \" \\ \/ */
			}
		}
		if (n + 2 > cap) {
			char *t = realloc(out, cap *= 2);

			if (!t) {
				free(out);
				return jfail(p, "out of memory");
			}
			out = t;
		}
		out[n++] = c;
	}
	p->s++;
	out[n] = 0;
	return out;
}

static struct oisp_json *value(struct jp *p);

static struct oisp_json *container(struct jp *p, int obj)
{
	struct oisp_json *j = node(obj ? OJ_OBJ : OJ_ARR), **tail;
	char close = obj ? '}' : ']';

	if (!j)
		return jfail(p, "out of memory");
	tail = &j->child;
	p->s++;
	ws(p);
	if (*p->s == close) {
		p->s++;
		return j;
	}
	for (;;) {
		char *key = NULL;
		struct oisp_json *v;

		ws(p);
		if (obj) {
			if (*p->s != '"')
				goto bad;
			key = jstring(p);
			if (!key)
				goto bad;
			ws(p);
			if (*p->s != ':') {
				free(key);
				goto bad;
			}
			p->s++;
		}
		v = value(p);
		if (!v) {
			free(key);
			goto bad;
		}
		v->key = key;
		*tail = v;
		tail = &v->next;
		j->n++;
		ws(p);
		if (*p->s == ',') {
			p->s++;
			ws(p);
			if (*p->s == close) {		/* trailing comma */
				p->s++;
				return j;
			}
			continue;
		}
		if (*p->s == close) {
			p->s++;
			return j;
		}
		goto bad;
	}
bad:
	oisp_json_free(j);
	return jfail(p, obj ? "bad object" : "bad array");
}

static struct oisp_json *value(struct jp *p)
{
	struct oisp_json *j;
	char *end;

	ws(p);
	if (++p->depth > 64)
		return jfail(p, "nesting too deep");
	switch (*p->s) {
	case '{':
	case '[':
		j = container(p, *p->s == '{');
		break;
	case '"':
		j = node(OJ_STR);
		if (j && !(j->str = jstring(p))) {
			free(j);
			j = NULL;
		}
		break;
	case 't':
	case 'f':
	case 'n':
		if (!strncmp(p->s, "true", 4) || !strncmp(p->s, "false", 5)) {
			j = node(OJ_BOOL);
			if (j)
				j->num = *p->s == 't';
			p->s += *p->s == 't' ? 4 : 5;
		} else if (!strncmp(p->s, "null", 4)) {
			j = node(OJ_NULL);
			p->s += 4;
		} else {
			j = jfail(p, "unexpected token");
		}
		break;
	default:
		j = node(OJ_NUM);
		if (!j)
			break;
		j->num = strtod(p->s, &end);
		if (end == p->s) {
			free(j);
			j = jfail(p, "unexpected character");
		}
		p->s = end;
	}
	p->depth--;
	return j;
}

struct oisp_json *oisp_json_parse(const char *text, char *err, size_t errlen)
{
	struct jp p = { text, 1, err, errlen, 0 };
	struct oisp_json *j;

	if (err && errlen)
		err[0] = 0;
	j = value(&p);
	if (!j)
		return NULL;
	ws(&p);
	if (*p.s) {
		oisp_json_free(j);
		return jfail(&p, "trailing characters");
	}
	return j;
}

struct oisp_json *oisp_json_load(const char *path, char *err, size_t errlen)
{
	FILE *f = fopen(path, "rb");
	struct oisp_json *j;
	long n;
	char *buf;

	if (!f) {
		snprintf(err, errlen, "cannot open %s", path);
		return NULL;
	}
	fseek(f, 0, SEEK_END);
	n = ftell(f);
	fseek(f, 0, SEEK_SET);
	buf = malloc(n + 1);
	if (!buf || fread(buf, 1, n, f) != (size_t)n) {
		free(buf);
		fclose(f);
		snprintf(err, errlen, "cannot read %s", path);
		return NULL;
	}
	fclose(f);
	buf[n] = 0;
	j = oisp_json_parse(buf, err, errlen);
	free(buf);
	return j;
}

void oisp_json_free(struct oisp_json *j)
{
	while (j) {
		struct oisp_json *next = j->next;

		oisp_json_free(j->child);
		free(j->str);
		free(j->key);
		free(j);
		j = next;
	}
}

struct oisp_json *oisp_json_get(const struct oisp_json *obj, const char *key)
{
	struct oisp_json *c;

	if (!obj || obj->type != OJ_OBJ)
		return NULL;
	for (c = obj->child; c; c = c->next)
		if (!strcmp(c->key, key))
			return c;
	return NULL;
}

struct oisp_json *oisp_json_at(const struct oisp_json *arr, int i)
{
	struct oisp_json *c;

	if (!arr || arr->type != OJ_ARR)
		return NULL;
	for (c = arr->child; c && i; c = c->next)
		i--;
	return c;
}

static int flatten(const struct oisp_json *j, double *out, int max, int n)
{
	const struct oisp_json *c;

	if (j->type == OJ_NUM || j->type == OJ_BOOL) {
		if (n >= max)
			return -1;
		out[n] = j->num;
		return n + 1;
	}
	if (j->type != OJ_ARR)
		return -1;
	for (c = j->child; c; c = c->next) {
		n = flatten(c, out, max, n);
		if (n < 0)
			return -1;
	}
	return n;
}

int oisp_json_numbers(const struct oisp_json *j, double *out, int max)
{
	return j ? flatten(j, out, max, 0) : -1;
}
