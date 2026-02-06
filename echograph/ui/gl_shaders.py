# echograph/ui/gl_shaders.py
# Pure data module: keep shader source strings here.

SHADERS = {
    "mesh_vertex": """
#version 330
uniform mat4 Mvp;
in vec3 in_position;
in vec3 in_normal;
in vec2 in_uv;
out vec3 v_norm;
out vec3 v_vert;
out vec2 v_uv;
void main() {
    gl_Position = Mvp * vec4(in_position, 1.0);
    v_norm = in_normal;
    v_vert = in_position;
    v_uv = in_uv;
}
""",

    "mesh_fragment": """
#version 330
uniform vec4 Color;
uniform vec3 Light;
uniform float LightIntensity;
uniform sampler2D Texture;
uniform int UseTexture;
uniform int UseLighting;
uniform int UseProcedural;
uniform int ProceduralMode;
uniform vec4 ProcParams;
uniform float ProcTime;
uniform sampler2D ProcGlyph;
uniform vec2 ProcGlyphGrid;
in vec3 v_norm;
in vec3 v_vert;
in vec2 v_uv;
out vec4 f_color;

float hash11(float n) {
    return fract(sin(n) * 43758.5453123);
}

float hash21(vec2 p) {
    return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453123);
}

vec4 proc_checker(vec2 uv) {
    float base = 8.0;
    float tiling = max(1.0, ProcParams.x);
    float pack_x = max(1.0, ProcParams.y);
    float pack_y = max(1.0, ProcParams.z);
    vec2 scale = vec2(tiling * pack_x * base, tiling * pack_y * base);
    vec2 uvw = fract(uv) * scale;
    vec2 cell = floor(uvw);
    float phase = mod(floor(ProcTime), 2.0);
    vec3 c0a = vec3(0.058, 0.090, 0.165);
    vec3 c1a = vec3(0.886, 0.910, 0.941);
    vec3 c0b = vec3(0.114, 0.306, 0.847);
    vec3 c1b = vec3(0.961, 0.620, 0.043);
    vec3 c0 = mix(c0a, c0b, phase);
    vec3 c1 = mix(c1a, c1b, phase);
    float checker = mod(cell.x + cell.y, 2.0);
    vec3 col = mix(c0, c1, checker);
    return vec4(col, 1.0);
}

vec4 proc_matrix(vec2 uv) {
    float base = 20.0;
    float tiling = max(1.0, ProcParams.x);
    float pack_x = max(1.0, ProcParams.y);
    float pack_y = max(1.0, ProcParams.z);
    float seed = ProcParams.w;
    float cols = max(1.0, tiling * pack_x * base);
    float rows = max(1.0, tiling * pack_y * base);
    vec2 grid = vec2(cols, rows);
    vec2 uvw = fract(uv) * grid;
    vec2 cell = floor(uvw);
    vec2 f = fract(uvw);
    float col = cell.x;
    float row = cell.y;
    float speed = mix(0.6, 1.8, hash11(col + seed));
    float trail = mix(8.0, 16.0, hash11(col + seed * 2.31));
    float head = mod(ProcTime * speed * rows + hash11(col + seed * 5.7) * rows, rows);
    float dy = head - row;
    if (dy < 0.0) dy += rows;
    float t = 1.0 - dy / max(trail, 1.0);
    vec3 bg = vec3(0.02, 0.04, 0.03);
    if (t <= 0.0) {
        return vec4(bg, 1.0);
    }
    vec2 pg = max(ProcGlyphGrid, vec2(1.0));
    float glyph_count = pg.x * pg.y;
    float glyph_idx = floor(hash21(vec2(col, row + floor(ProcTime * 4.0))) * glyph_count);
    vec2 gcell = vec2(mod(glyph_idx, pg.x), floor(glyph_idx / pg.x));
    float mirror = step(hash21(vec2(col + seed, row)), 0.22);
    if (mirror > 0.5) {
        f.x = 1.0 - f.x;
    }
    vec2 glyph_uv = (gcell + f) / pg;
    float glyph = texture(ProcGlyph, glyph_uv).r;
    vec3 head_col = vec3(0.73, 0.97, 0.82);
    vec3 tail_col = vec3(0.13, 0.77, 0.37);
    vec3 color = mix(tail_col, head_col, smoothstep(0.6, 1.0, t));
    vec3 rgb = mix(bg, color, glyph * t);
    return vec4(rgb, 1.0);
}

void main() {
    float lum = 1.0;
    if (UseLighting == 1) {
        lum = -dot(normalize(v_norm), normalize(v_vert + Light));
        lum = acos(lum) / 3.14159265;
        lum = clamp(lum, 0.0, 1.0);
        lum = lum * lum;
        lum = smoothstep(0.0, 1.0, lum);
        lum *= smoothstep(0.0, 80.0, v_vert.z) * 0.3 + 0.7;
        lum = lum * 0.8 + 0.2;
        lum = 0.2 + lum * max(LightIntensity, 0.0);
        lum = clamp(lum, 0.0, 10.0);
    }
    vec4 base = Color;
    if (UseProcedural == 1) {
        if (ProceduralMode == 1) {
            base = proc_matrix(v_uv);
        } else {
            base = proc_checker(v_uv);
        }
    } else {
        base = (UseTexture == 1) ? texture(Texture, v_uv) : Color;
    }
    f_color = vec4(base.rgb * lum, base.a);
}
""",

    "grid_vertex": """
#version 330
uniform mat4 Mvp;
uniform vec2 GridOffset;
in vec3 in_position;
out vec3 v_pos;
void main() {
    vec3 pos = in_position + vec3(GridOffset.x, 0.0, GridOffset.y);
    v_pos = pos;
    gl_Position = Mvp * vec4(pos, 1.0);
}
""",

    "grid_fragment": """
#version 330
uniform vec4 Color;
uniform float GridSpacing;
uniform float MajorStep;
uniform float MajorBoost;
uniform float FadeStart;
uniform float FadeEnd;
uniform vec2 FadeOrigin;
in vec3 v_pos;
out vec4 f_color;
void main() {
    vec3 base = Color.rgb;
    float alpha = Color.a;
    float spacing = max(GridSpacing, 1e-6);
    float ix = abs(v_pos.x) / spacing;
    float iz = abs(v_pos.z) / spacing;
    float fx = abs(ix - round(ix));
    float fz = abs(iz - round(iz));
    float line_idx = (fz <= fx) ? iz : ix;
    float major_step = max(MajorStep, 1.0);
    float modv = mod(round(line_idx), major_step);
    float is_major = (modv < 0.5) ? 1.0 : 0.0;
    vec3 major = clamp(base * MajorBoost, 0.0, 1.0);
    vec3 rgb = mix(base, major, is_major);
    vec2 rel = v_pos.xz - FadeOrigin;
    float dist = length(rel);
    float fade = 1.0;
    if (FadeEnd > FadeStart) {
        fade = 1.0 - smoothstep(FadeStart, FadeEnd, dist);
    }
    rgb *= fade;
    f_color = vec4(rgb, alpha * fade);
}
""",

    "wire_vertex": """
#version 330
uniform mat4 Mvp;
uniform vec2 Viewport;
uniform float LineWidth;
in vec3 in_pos;
in vec3 in_start;
in vec3 in_end;
in float in_side;
void main() {
    vec4 clip_start = Mvp * vec4(in_start, 1.0);
    vec4 clip_end = Mvp * vec4(in_end, 1.0);
    float s0 = clip_start.z + clip_start.w;
    float s1 = clip_end.z + clip_end.w;
    if (s0 < 0.0 && s1 < 0.0) {
        gl_Position = vec4(2.0, 2.0, 2.0, 1.0);
        return;
    }
    if (s0 < 0.0 || s1 < 0.0) {
        float denom = s0 - s1;
        float t = abs(denom) > 1e-6 ? (s0 / denom) : 0.0;
        t = clamp(t, 0.0, 1.0);
        if (s0 < 0.0) {
            clip_start = mix(clip_start, clip_end, t);
        } else {
            clip_end = mix(clip_start, clip_end, t);
        }
    }
    float ws = max(1e-6, clip_start.w);
    float we = max(1e-6, clip_end.w);
    vec2 ndc0 = clip_start.xy / ws;
    vec2 ndc1 = clip_end.xy / we;
    vec2 dir = ndc1 - ndc0;
    float len = length(dir);
    vec2 perp = len > 1e-6 ? vec2(-dir.y, dir.x) / len : vec2(0.0, 1.0);
    vec2 pixel = vec2(2.0 / max(Viewport.x, 1.0), 2.0 / max(Viewport.y, 1.0));
    vec2 offset = perp * (LineWidth * 0.5) * pixel;
    float d0 = distance(in_pos, in_start);
    float d1 = distance(in_pos, in_end);
    vec4 clip_pos = (d0 <= d1) ? clip_start : clip_end;
    float wp = max(1e-6, clip_pos.w);
    vec2 ndc_pos = clip_pos.xy / wp;
    vec2 ndc_out = ndc_pos + offset * in_side;
    float ndc_z = clip_pos.z / wp;
    gl_Position = vec4(ndc_out, ndc_z, 1.0);
}
""",

    "wire_fragment": """
#version 330
uniform vec4 Color;
out vec4 f_color;
void main() {
    f_color = Color;
}
""",

    "splat_vertex": """
#version 330
uniform mat4 Mvp;
uniform float SplatSizeMul;

in vec3 in_pos;
in vec4 in_col;
in float in_rad;

out vec4 v_col;

void main() {
    vec4 clip = Mvp * vec4(in_pos, 1.0);
    gl_Position = clip;

    float w = max(1e-6, clip.w);
    float px = in_rad * SplatSizeMul * (200.0 / w);
    gl_PointSize = clamp(px, 1.0, 32.0);

    v_col = in_col;
}
""",

    "splat_fragment": """
#version 330
in vec4 v_col;
out vec4 f_color;
void main() {
    vec2 p = gl_PointCoord * 2.0 - 1.0;
    float r2 = dot(p, p);
    if (r2 > 1.0) discard;

    float a = exp(-r2 * 1.2);
    float edge = smoothstep(1.0, 0.7, r2);
    a *= edge;

    f_color = vec4(v_col.rgb * a, a);
}
""",

    "splatq_vertex": """
#version 330

uniform mat4 Proj;
uniform mat4 View;
uniform mat4 Model;
uniform float SplatWorldScale;

in vec2 in_corner;
in vec3 in_pos;
in vec4 in_col;
in float in_rad;
in vec3 in_scale3;
in vec4 in_rot;

out vec2 v_uv;
out vec4 v_col;

vec3 quat_rotate(vec3 v, vec4 q) {
    vec3 t = 2.0 * cross(q.xyz, v);
    return v + q.w * t + cross(q.xyz, t);
}

void main() {
    vec4 view_p = View * Model * vec4(in_pos, 1.0);

    float s = in_rad * SplatWorldScale;
    mat3 VM = mat3(View * Model);

    vec3 ax = VM * quat_rotate(vec3(1.0, 0.0, 0.0), in_rot) * (in_scale3.x * s);
    vec3 ay = VM * quat_rotate(vec3(0.0, 1.0, 0.0), in_rot) * (in_scale3.y * s);
    vec3 az = VM * quat_rotate(vec3(0.0, 0.0, 1.0), in_rot) * (in_scale3.z * s);

    vec2 a0 = ax.xy;
    vec2 a1 = ay.xy;
    vec2 a2 = az.xy;

    float c00 = dot(a0, vec2(a0.x, 0.0)) + dot(a1, vec2(a1.x, 0.0)) + dot(a2, vec2(a2.x, 0.0));
    float c01 = a0.x*a0.y + a1.x*a1.y + a2.x*a2.y;
    float c11 = dot(a0, vec2(0.0, a0.y)) + dot(a1, vec2(0.0, a1.y)) + dot(a2, vec2(0.0, a2.y));

    float tr  = c00 + c11;
    float det = c00*c11 - c01*c01;
    float disc = max(tr*tr*0.25 - det, 0.0);
    float root = sqrt(disc);

    float l1 = max(tr*0.5 + root, 1e-12);
    float l2 = max(tr*0.5 - root, 1e-12);

    vec2 v1 = vec2(c01, l1 - c00);
    if (length(v1) < 1e-8) v1 = vec2(1.0, 0.0);
    v1 = normalize(v1);
    vec2 v2 = vec2(-v1.y, v1.x);

    float r1 = sqrt(l1);
    float r2 = sqrt(l2);

    view_p.xy += v1 * (in_corner.x * r1) + v2 * (in_corner.y * r2);

    v_uv = in_corner;
    v_col = in_col;

    gl_Position = Proj * view_p;
}
""",

    "splatq_fragment": """
#version 330
in vec2 v_uv;
in vec4 v_col;
out vec4 f_color;

void main() {
    float r2 = dot(v_uv, v_uv);
    if (r2 > 1.0) discard;

    float a = exp(-r2 * 2.0);
    a *= v_col.a;

    if (a < 1e-4) discard;

    f_color = vec4(v_col.rgb * a, a);
}
""",

    "example_vertex_330": """
#version 330
layout(location = 0) in vec3 a_position;
layout(location = 1) in vec4 a_color;
uniform mat4 u_proj;
uniform mat4 u_view;
uniform mat4 u_trans;
out vec4 v_color;
void main() {
    gl_Position = u_proj * u_view * u_trans * vec4(a_position, 1.0);
    v_color = a_color;
}
""",
    "example_fragment_330": """
#version 330
in vec4 v_color;
out vec4 fragColor;
void main() {
    fragColor = v_color;
}
""",
    "example_vertex_legacy": """
attribute vec3 a_position;
attribute vec4 a_color;
uniform mat4 u_proj;
uniform mat4 u_view;
uniform mat4 u_trans;
varying vec4 v_color;
void main() {
    gl_Position = u_proj * u_view * u_trans * vec4(a_position, 1.0);
    v_color = a_color;
}
""",
    "example_fragment_legacy": """
varying vec4 v_color;
void main() {
    gl_FragColor = v_color;
}
""",

}
