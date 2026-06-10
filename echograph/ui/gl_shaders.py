# echograph/ui/gl_shaders.py
# Pure data module: keep shader source strings here.

SHADERS = {
    "mesh_vertex": """
#version 330
uniform mat4 Mvp;
uniform mat4 Model;
uniform mat4 LightMvp;
uniform int UseInstancing;
in vec3 in_position;
in vec3 in_normal;
in vec2 in_uv;
in vec4 in_color;
in vec4 in_instance_col0;
in vec4 in_instance_col1;
in vec4 in_instance_col2;
in vec4 in_instance_col3;
out vec3 v_norm;
out vec3 v_vert;
out vec2 v_uv;
out vec3 v_world_norm;
out vec3 v_world_pos;
out vec4 v_color;
out vec4 v_shadow_pos;
vec3 safe_normalize(vec3 v) {
    float len2 = dot(v, v);
    if (len2 <= 1e-10) {
        return vec3(0.0, 0.0, 1.0);
    }
    return v * inversesqrt(len2);
}
mat3 normal_matrix(mat4 m) {
    mat3 n = mat3(m);
    float det = determinant(n);
    if (abs(det) <= 1e-8) {
        return n;
    }
    return transpose(inverse(n));
}
void main() {
    mat4 instance_model = mat4(
        in_instance_col0,
        in_instance_col1,
        in_instance_col2,
        in_instance_col3
    );
    if (UseInstancing == 0) {
        instance_model = mat4(1.0);
    }
    vec4 local = instance_model * vec4(in_position, 1.0);
    mat4 world_model = Model * instance_model;
    vec4 world = world_model * vec4(in_position, 1.0);
    gl_Position = Mvp * local;
    v_norm = safe_normalize(normal_matrix(instance_model) * in_normal);
    v_vert = local.xyz;
    v_uv = in_uv;
    v_world_norm = safe_normalize(normal_matrix(world_model) * in_normal);
    v_world_pos = world.xyz;
    v_color = in_color;
    v_shadow_pos = LightMvp * world;
}
""",

    "mesh_fragment": """
#version 330
uniform vec4 Color;
uniform vec3 Light;
uniform vec3 LightDir;
uniform vec3 LightPos;
uniform int LightType;
uniform float LightIntensity;
uniform float AmbientLight;
uniform float LightRange;
uniform int UsePointShadowAtlas;
uniform mat4 PointShadowMvp0;
uniform mat4 PointShadowMvp1;
uniform mat4 PointShadowMvp2;
uniform mat4 PointShadowMvp3;
uniform mat4 PointShadowMvp4;
uniform mat4 PointShadowMvp5;
uniform float SpotCosInner;
uniform float SpotCosOuter;
uniform sampler2D Texture;
uniform sampler2D ShadowMap;
uniform sampler2D ShadowIdMap;
uniform int UseTexture;
uniform int UseVertexColor;
uniform int UseMaterial;
uniform int UseLighting;
uniform int UseShadows;
uniform int UseSelfShadows;
uniform int UseProcedural;
uniform int UseProceduralLayer;
uniform int UseVolumeMask;
uniform mat4 VolumeInv;
uniform mat4 Model;
uniform float ShadowBias;
uniform float ShadowDarkness;
uniform float ShadowReceiverId;
uniform vec2 ShadowMapSize;
uniform float MaterialTransparency;
uniform float MaterialIor;
uniform vec3 MaterialTint;
uniform float MaterialFresnelAmount;
uniform vec3 MaterialFresnelColor;
uniform sampler2D SceneColorTex;
uniform int UseSceneRefraction;
uniform vec2 ScreenSize;
uniform vec3 CameraWorldPos;
uniform int ProceduralMode;
uniform vec4 ProcParams;
uniform float ProcSeed;
uniform float ProcAnimSpeed;
uniform float ProcEmissive;
uniform float ProcLightMix;
uniform float ProcSoftness;
uniform vec2 ProcOffset;
uniform float ProcPan;
uniform float ProcLifeMin;
uniform float ProcLifeMax;
uniform float ProcChainMin;
uniform float ProcChainMax;
uniform int ProceduralMode2;
uniform vec4 ProcParams2;
uniform float ProcSeed2;
uniform float ProcAnimSpeed2;
uniform float ProcEmissive2;
uniform float ProcLightMix2;
uniform float ProcSoftness2;
uniform vec2 ProcOffset2;
uniform float ProcPan2;
uniform float ProcLifeMin2;
uniform float ProcLifeMax2;
uniform float ProcChainMin2;
uniform float ProcChainMax2;
uniform float ProcTime;
uniform vec4 ProcBg;
uniform int ProcBgEnabled;
uniform vec4 ProcBg2;
uniform int ProcBgEnabled2;
uniform sampler2D ProcGlyph;
uniform vec2 ProcGlyphGrid;
uniform float ProcGlyphCount;
in vec3 v_norm;
in vec3 v_vert;
in vec2 v_uv;
in vec3 v_world_norm;
in vec3 v_world_pos;
in vec4 v_color;
in vec4 v_shadow_pos;
out vec4 f_color;

vec3 safe_normalize(vec3 v) {
    float len2 = dot(v, v);
    if (len2 <= 1e-10) {
        return vec3(0.0, 0.0, 1.0);
    }
    return v * inversesqrt(len2);
}

vec3 face_normal(vec3 n) {
    return safe_normalize(n);
}

float hash11(float n) {
    return fract(sin(n) * 43758.5453123);
}

float hash21(vec2 p) {
    return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453123);
}

float volume_mask() {
    if (UseVolumeMask == 0) {
        return 1.0;
    }
    vec4 world = Model * vec4(v_vert, 1.0);
    vec4 local = VolumeInv * world;
    vec3 q = abs(local.xyz);
    float m = max(q.x, max(q.y, q.z));
    return step(m, 0.5);
}

vec4 proc_checker_params(vec2 uv, vec4 params, float anim_speed, vec2 cell_offset, vec4 bg, int bg_enabled) {
    float base = 8.0;
    float tiling = max(1.0, params.x);
    float pack_x = max(1.0, params.y);
    float pack_y = max(1.0, params.z);
    float anim_t = ProcTime * max(0.0, anim_speed);
    vec2 scale = vec2(tiling * pack_x * base, tiling * pack_y * base);
    vec2 shift = cell_offset / max(scale, vec2(1.0));
    vec2 uvw = fract(uv + shift) * scale;
    vec2 cell = floor(uvw);
    float phase = mod(floor(anim_t), 2.0);
    vec3 c0a = vec3(0.058, 0.090, 0.165);
    vec3 c1a = vec3(0.886, 0.910, 0.941);
    vec3 c0b = vec3(0.114, 0.306, 0.847);
    vec3 c1b = vec3(0.961, 0.620, 0.043);
    vec3 c0 = mix(c0a, c0b, phase);
    vec3 c1 = mix(c1a, c1b, phase);
    float a0 = 1.0;
    if (bg_enabled == 1) {
        c0 = bg.rgb;
        a0 = bg.a;
    }
    float checker = mod(cell.x + cell.y, 2.0);
    vec3 col = mix(c0, c1, checker);
    float alpha = mix(a0, 1.0, checker);
    return vec4(col, alpha);
}

vec4 proc_matrix_params(
    vec2 uv,
    vec4 params,
    float seed_in,
    float anim_speed,
    vec4 bg_in,
    int bg_enabled,
    float softness,
    vec2 cell_offset,
    float pan_enabled,
    float life_min,
    float life_max,
    float chain_min,
    float chain_max
) {
    float base = 20.0;
    float tiling = max(1.0, params.x);
    float pack_x = max(1.0, params.y);
    float pack_y = max(1.0, params.z);
    float seed = fract(seed_in * 0.000244140625);
    float raw_t = ProcTime;
    float anim_t = raw_t * max(0.0, anim_speed);
    float cols = max(1.0, tiling * pack_x * base);
    float rows = max(1.0, tiling * pack_y * base);
    vec2 shift = cell_offset / max(vec2(cols, rows), vec2(1.0));
    vec2 p = fract(uv + shift) * vec2(cols, rows);
    float dir_id = params.w;
    float horiz = step(1.5, dir_id);
    float neg = step(0.5, mod(dir_id, 2.0));
    float dir = mix(1.0, -1.0, neg);
    float stream_id = mix(floor(p.x), floor(p.y), horiz);
    float along = mix(p.y, p.x, horiz);
    float along_cells = mix(rows, cols, horiz);
    float speed = mix(0.6, 1.8, hash11(stream_id * 0.73 + seed * 91.7));
    float trail = mix(6.0, 18.0, hash11(stream_id * 1.31 + seed * 57.3));
    float col_phase = hash11(stream_id * 1.19 + seed * 53.1) * along_cells;
    float life_lo = max(0.1, life_min);
    float life_hi = max(life_lo, life_max);
    float life = mix(life_lo, life_hi, hash11(stream_id * 1.61 + seed * 9.7));
    trail *= mix(0.8, 1.6, life / 10.0);
    trail = clamp(trail, 6.0, 26.0);
    float fade = mix(1.1025, 2.646, hash11(stream_id * 2.11 + seed * 7.3));
    float dmin = min(chain_min, chain_max);
    float dmax = max(chain_min, chain_max);
    float dead = mix(dmin, dmax, hash11(stream_id * 2.71 + seed * 5.1));
    float cycle = life + fade + dead;
    float offset = hash11(stream_id * 3.17 + seed * 17.1) * cycle;
    float tcol = raw_t + offset;
    float base_cycle = floor(tcol / cycle) * cycle;
    float t_in = tcol - base_cycle;
    vec4 bg = vec4(0.01, 0.03, 0.01, 1.0);
    if (bg_enabled == 1) {
        bg = bg_in;
    }
    float t_alive = min(t_in, life);
    float travel = t_alive * max(0.0, anim_speed) * max(0.0, speed);
    float grow = clamp(travel / max(trail, 1.0), 0.0, 1.0);
    float trail_len = min(trail, max(1.0, travel + 1.0));
    float fade_out = 1.0;
    if (t_in >= life) {
        float fade_t = (t_in - life) / max(0.0001, fade);
        fade_out = 1.0 - clamp(fade_t, 0.0, 1.0);
    }
    float row_seed = floor(along);
    float stick_seed = hash21(vec2(stream_id * 0.37 + seed * 17.0, row_seed * 0.73 + seed * 3.0));
    float stick = step(stick_seed, 0.18);
    float stick_rand = hash11(stream_id * 7.13 + row_seed * 2.13 + seed * 3.7);
    float stick_len = trail * mix(0.4, 1.5, stick_rand);
    trail_len = min(trail_len + stick * stick_len * grow, along_cells * 0.9);
    float after_fade = 0.0;
    if (t_in > life + fade && stick > 0.5) {
        float after_t = (t_in - (life + fade)) / max(0.0001, dead);
        after_fade = 1.0 - clamp(after_t, 0.0, 1.0);
    }
    float live = max(fade_out, after_fade * stick);
    float motion_t = (base_cycle + t_alive - offset) * max(0.0, anim_speed);
    float scroll = motion_t * speed * dir + col_phase;
    if (pan_enabled < 0.5) {
        scroll = floor(scroll + 0.0001);
    }
    float stream_pos = along - scroll;
    stream_pos = mix(stream_pos, along, stick);
    float row = floor(stream_pos);
    vec2 f = vec2(fract(p.x), fract(p.y));
    if (horiz > 0.5) {
        // Rotate glyphs 90 degrees when travelling left/right.
        if (dir > 0.0) {
            f = vec2(f.y, 1.0 - f.x);
        } else {
            f = vec2(1.0 - f.y, f.x);
        }
    }
    float head = mod(scroll, along_cells);
    float dy = (dir > 0.0) ? (head - along) : (along - head);
    if (dy < 0.0) dy += along_cells;
    float t = 1.0 - dy / max(trail_len, 1.0);
    if (t <= 0.0) {
        return bg;
    }
    vec2 pg = max(ProcGlyphGrid, vec2(1.0));
    float glyph_count = max(1.0, min(pg.x * pg.y, ProcGlyphCount));
    float hold = mix(0.3, 3.0, hash11(stream_id * 1.91 + row * 0.73 + seed * 11.0));
    hold *= mix(1.0, 2.8, stick);
    float glyph_anim = floor(motion_t / max(0.05, hold));
    vec2 gh = vec2(
        stream_id + row * 0.11 + glyph_anim * 0.07 + seed * 37.0,
        row + stream_id * 0.19 + glyph_anim * 0.13 + seed * 61.0
    );
    float glyph_idx = floor(hash21(gh) * glyph_count);
    vec2 gcell = vec2(mod(glyph_idx, pg.x), floor(glyph_idx / pg.x));
    float mirror = step(hash21(vec2(stream_id * 0.71 + seed * 19.0, row * 1.37 + seed * 3.0)), 0.22);
    if (mirror > 0.5) {
        f.x = 1.0 - f.x;
    }
    vec2 pad = vec2(0.08, 0.08);
    vec2 glyph_uv = (gcell + mix(pad, vec2(1.0) - pad, f)) / pg;
    float glyph = texture(ProcGlyph, glyph_uv).r;
    float soft = clamp(softness, 0.0, 1.0);
    float edge = fwidth(glyph) * mix(0.8, 3.0, soft);
    glyph = smoothstep(0.20 - edge, 0.86 + edge, glyph);
    vec3 head_col = vec3(0.78, 0.96, 0.82);
    vec3 tail_col = vec3(0.11, 0.78, 0.14);
    vec3 color = mix(tail_col, head_col, smoothstep(0.85, 0.98, t));
    float t_fade = t * live;
    vec3 rgb = mix(bg.rgb, color, glyph * t_fade);
    float alpha = mix(bg.a, 1.0, glyph * t_fade);
    return vec4(rgb, alpha);
}

vec3 apply_lighting(vec3 rgb, float lum, float light_mix, float emissive) {
    float mixv = clamp(light_mix, 0.0, 1.0);
    vec3 lit = mix(rgb, rgb * lum, mixv);
    lit += rgb * max(0.0, emissive);
    return lit;
}

float sample_shadow_factor(vec4 shadow_pos, vec3 normal, vec3 light_vec) {
    if (UseShadows == 0) {
        return 1.0;
    }
    vec2 atlas_base = vec2(0.0);
    vec2 atlas_scale = vec2(1.0);
    vec4 sample_pos = shadow_pos;
    if (UsePointShadowAtlas == 1 && LightType == 1) {
        vec3 rel = v_world_pos - LightPos;
        vec3 a = abs(rel);
        mat4 face_mvp = PointShadowMvp0;
        if (a.x >= a.y && a.x >= a.z) {
            if (rel.x >= 0.0) {
                face_mvp = PointShadowMvp0;
                atlas_base = vec2(0.0, 0.0);
            } else {
                face_mvp = PointShadowMvp1;
                atlas_base = vec2(0.33333334, 0.0);
            }
        } else if (a.y >= a.z) {
            if (rel.y >= 0.0) {
                face_mvp = PointShadowMvp2;
                atlas_base = vec2(0.6666667, 0.0);
            } else {
                face_mvp = PointShadowMvp3;
                atlas_base = vec2(0.0, 0.5);
            }
        } else {
            if (rel.z >= 0.0) {
                face_mvp = PointShadowMvp4;
                atlas_base = vec2(0.33333334, 0.5);
            } else {
                face_mvp = PointShadowMvp5;
                atlas_base = vec2(0.6666667, 0.5);
            }
        }
        atlas_scale = vec2(0.33333334, 0.5);
        sample_pos = face_mvp * vec4(v_world_pos, 1.0);
    }
    float w = sample_pos.w;
    if (abs(w) <= 1e-6) {
        return 1.0;
    }
    vec3 proj = sample_pos.xyz / w;
    vec3 uvw = proj * 0.5 + 0.5;
    if (uvw.x < 0.0 || uvw.x > 1.0 || uvw.y < 0.0 || uvw.y > 1.0 || uvw.z < 0.0 || uvw.z > 1.0) {
        return 1.0;
    }
    vec3 n = safe_normalize(normal);
    vec3 l = safe_normalize(light_vec);
    float ndotl = clamp(abs(dot(n, l)), 0.0, 1.0);
    float bias = max(ShadowBias * (1.0 - ndotl), ShadowBias * 0.25);
    vec2 texel = 1.0 / max(ShadowMapSize, vec2(1.0));
    float visible = 0.0;
    float sample_count = 0.0;
    int radius = (LightType == 3) ? 2 : 1;
    for (int x = -2; x <= 2; x++) {
        for (int y = -2; y <= 2; y++) {
            if (abs(x) > radius || abs(y) > radius) {
                continue;
            }
            vec2 sample_uv = atlas_base + uvw.xy * atlas_scale + vec2(float(x), float(y)) * texel;
            if (UsePointShadowAtlas == 1 && LightType == 1) {
                vec2 tile_min = atlas_base + texel * 0.5;
                vec2 tile_max = atlas_base + atlas_scale - texel * 0.5;
                sample_uv = clamp(sample_uv, tile_min, tile_max);
            }
            float closest = texture(ShadowMap, sample_uv).r;
            float sample_visible = ((uvw.z - bias) <= closest) ? 1.0 : 0.0;
            if (UseSelfShadows == 0 && sample_visible < 1.0 && ShadowReceiverId > 0.0) {
                float caster_id = texture(ShadowIdMap, sample_uv).r;
                if (abs(caster_id - ShadowReceiverId) < 0.000008) {
                    sample_visible = 1.0;
                }
            }
            visible += sample_visible;
            sample_count += 1.0;
        }
    }
    visible /= max(sample_count, 1.0);
    return mix(1.0 - clamp(ShadowDarkness, 0.0, 1.0), 1.0, visible);
}

vec3 light_vector_at(vec3 world_pos, out float attenuation) {
    attenuation = 1.0;
    if (LightType == 0) {
        return safe_normalize(LightDir);
    }
    vec3 to_light = LightPos - world_pos;
    float dist = length(to_light);
    vec3 l = (dist > 1e-5) ? (to_light / dist) : safe_normalize(LightDir);
    float range = max(LightRange, 0.001);
    float d = dist / range;
    attenuation = 1.0 / (1.0 + 4.0 * d * d);
    if (LightType == 2) {
        float cone = dot(l, safe_normalize(LightDir));
        attenuation *= smoothstep(SpotCosOuter, SpotCosInner, cone);
    } else if (LightType == 3) {
        attenuation *= 0.85;
    }
    return l;
}

vec4 composite_over(vec4 base, vec4 over) {
    float oa = clamp(over.a, 0.0, 1.0);
    float ba = clamp(base.a, 0.0, 1.0);
    float out_a = oa + ba * (1.0 - oa);
    vec3 out_rgb_p = over.rgb * oa + base.rgb * ba * (1.0 - oa);
    vec3 out_rgb = (out_a > 1e-6) ? (out_rgb_p / out_a) : vec3(0.0);
    return vec4(out_rgb, out_a);
}

void main() {
    if (UseVolumeMask == 1) {
        if (volume_mask() < 0.5) {
            discard;
        }
    }
    vec4 base = Color;
    float lum = 1.0;
    float material_light = 1.0;
    vec3 view_dir = safe_normalize(CameraWorldPos - v_world_pos);
    vec3 shading_n = face_normal(v_world_norm);
    if (UseLighting == 1) {
        vec3 n = shading_n;
        if (LightType != 0 && !gl_FrontFacing) {
            n = -n;
        }
        float light_attenuation = 1.0;
        vec3 l = light_vector_at(v_world_pos, light_attenuation);
        float surface_gate = (LightType == 0 && !gl_FrontFacing) ? 0.0 : 1.0;
        float diffuse = max(dot(n, l), 0.0) * surface_gate * light_attenuation;
        float shadow_visibility = sample_shadow_factor(v_shadow_pos, n, l);
        float ambient = clamp(AmbientLight, 0.0, 1.0);
        float direct = diffuse * 0.86 * max(LightIntensity, 0.0) * shadow_visibility;
        lum = ambient + direct;
        lum = clamp(lum, 0.0, 10.0);
        material_light = clamp(lum, 0.0, 1.0);
    }
    if (UseProcedural == 1) {
        vec4 p0 = (ProceduralMode == 1)
            ? proc_matrix_params(
                v_uv,
                ProcParams,
                ProcSeed,
                ProcAnimSpeed,
                ProcBg,
                ProcBgEnabled,
                ProcSoftness,
                ProcOffset,
                ProcPan,
                ProcLifeMin,
                ProcLifeMax,
                ProcChainMin,
                ProcChainMax
            )
            : proc_checker_params(v_uv, ProcParams, ProcAnimSpeed, ProcOffset, ProcBg, ProcBgEnabled);
        vec3 rgb0 = apply_lighting(p0.rgb, lum, ProcLightMix, ProcEmissive);
        vec4 c0 = vec4(rgb0, p0.a);
        if (UseProceduralLayer == 1) {
            vec4 p1 = (ProceduralMode2 == 1)
                ? proc_matrix_params(
                    v_uv,
                    ProcParams2,
                    ProcSeed2,
                    ProcAnimSpeed2,
                    ProcBg2,
                    ProcBgEnabled2,
                    ProcSoftness2,
                    ProcOffset2,
                    ProcPan2,
                    ProcLifeMin2,
                    ProcLifeMax2,
                    ProcChainMin2,
                    ProcChainMax2
                )
                : proc_checker_params(v_uv, ProcParams2, ProcAnimSpeed2, ProcOffset2, ProcBg2, ProcBgEnabled2);
            vec3 rgb1 = apply_lighting(p1.rgb, lum, ProcLightMix2, ProcEmissive2);
            vec4 c1 = vec4(rgb1, p1.a);
            base = composite_over(c0, c1);
        } else {
            base = c0;
        }
    } else {
        if (UseVertexColor == 1) {
            base = vec4(clamp(v_color.rgb, 0.0, 1.0), 1.0);
        } else {
            base = (UseTexture == 1) ? texture(Texture, v_uv) : Color;
        }
        float light_mix = clamp(ProcLightMix, 0.0, 1.0);
        vec3 lit_rgb = mix(base.rgb, base.rgb * lum, light_mix);
        base = vec4(clamp(lit_rgb, 0.0, 1.0), base.a);
    }
    if (UseMaterial == 1) {
        base.a = max(base.a, 1.0);
        float transmission = clamp(MaterialTransparency, 0.0, 1.0);
        float ior = max(MaterialIor, 1.0);
        float refraction_strength = clamp((ior - 1.0) / 1.5, 0.0, 1.0);
        vec3 tint = clamp(MaterialTint, vec3(0.0), vec3(1.0));
        float fresnel_amount = clamp(MaterialFresnelAmount, 0.0, 1.0);
        vec3 fresnel_color = clamp(MaterialFresnelColor, vec3(0.0), vec3(1.0));
        vec3 n = shading_n;
        float edge = pow(1.0 - clamp(abs(n.z), 0.0, 1.0), 1.6);
        vec3 material_rgb = clamp(base.rgb * mix(vec3(1.0), tint, 0.58), 0.0, 1.0);
        float refraction_present = 0.0;
        if (UseSceneRefraction == 1 && refraction_strength > 0.001) {
            refraction_present = 1.0;
            vec2 safe_screen = max(ScreenSize, vec2(1.0, 1.0));
            vec2 screen_uv = gl_FragCoord.xy / safe_screen;
            float refract_strength = pow(refraction_strength, 1.5);
            vec3 incident = -view_dir;
            vec3 refracted = refract(incident, n, 1.0 / max(ior, 1.001));
            vec2 incident_slope = incident.xy / max(0.35, abs(incident.z));
            vec2 refracted_slope = refracted.xy / max(0.35, abs(refracted.z));
            vec2 bend_delta = refracted_slope - incident_slope;
            vec2 refract_offset = bend_delta * (0.010 * refract_strength) * (0.18 + transmission * 0.34) * (0.22 + edge * 0.16);
            vec2 warped_uv0 = clamp(screen_uv + refract_offset, vec2(0.001), vec2(0.999));
            vec2 warped_uv1 = clamp(screen_uv - refract_offset * 0.45, vec2(0.001), vec2(0.999));
            vec3 scene_rgb0 = texture(SceneColorTex, warped_uv0).rgb;
            vec3 scene_rgb1 = texture(SceneColorTex, warped_uv1).rgb;
            vec3 scene_rgb = mix(scene_rgb0, scene_rgb1, 0.35);
            vec3 transmitted_rgb = scene_rgb * mix(vec3(1.0), tint, 0.18 + transmission * 0.22);
            material_rgb = mix(
                material_rgb * mix(vec3(1.0), tint, 0.08),
                clamp(transmitted_rgb, 0.0, 1.0),
                clamp(0.88 + transmission * 0.08, 0.0, 0.98)
            );
        }
        vec3 edge_rgb = clamp(material_rgb * mix(vec3(1.0), tint, 0.30), 0.0, 1.0);
        material_rgb = mix(material_rgb, edge_rgb, clamp(edge * (0.22 + refraction_strength * 0.12), 0.0, 0.34));
        if (fresnel_amount > 0.001) {
            float fresnel_term = pow(1.0 - clamp(abs(dot(n, view_dir)), 0.0, 1.0), 3.0);
            vec3 fresnel_rgb = fresnel_color * max(material_light, refraction_present);
            material_rgb = mix(material_rgb, fresnel_rgb, clamp(fresnel_term * fresnel_amount, 0.0, 1.0));
        }
        base.rgb = material_rgb;
        float material_alpha = max(0.0, 1.0 - transmission);
        material_alpha += 0.10 + refraction_strength * 0.08;
        material_alpha += edge * (0.14 + refraction_strength * 0.10);
        float alpha_out = clamp(material_alpha, 0.18, 0.92);
        if (refraction_present > 0.5) {
            alpha_out = mix(alpha_out, 0.96, clamp(0.62 + transmission * 0.24, 0.0, 0.98));
        }
        base.a *= alpha_out;
    }
    if (base.a <= 0.001) {
        discard;
    }
    f_color = vec4(clamp(base.rgb, 0.0, 1.0), base.a);
}
""",

    "shadow_vertex": """
#version 330
uniform mat4 LightMvp;
uniform mat4 Model;
uniform int UseInstancing;
in vec3 in_position;
in vec4 in_instance_col0;
in vec4 in_instance_col1;
in vec4 in_instance_col2;
in vec4 in_instance_col3;
void main() {
    mat4 instance_model = mat4(
        in_instance_col0,
        in_instance_col1,
        in_instance_col2,
        in_instance_col3
    );
    if (UseInstancing == 0) {
        instance_model = mat4(1.0);
    }
    gl_Position = LightMvp * Model * instance_model * vec4(in_position, 1.0);
}
""",

    "shadow_fragment": """
#version 330
uniform float ShadowCasterId;
layout(location = 0) out float f_shadow_id;
void main() {
    f_shadow_id = ShadowCasterId;
}
""",

    "grid_vertex": """
#version 330
uniform mat4 Mvp;
uniform mat4 LightMvp;
uniform vec2 GridOffset;
in vec3 in_position;
out vec3 v_pos;
out vec4 v_shadow_pos;
void main() {
    vec3 pos = in_position + vec3(GridOffset.x, 0.0, GridOffset.y);
    v_pos = pos;
    v_shadow_pos = LightMvp * vec4(pos, 1.0);
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
uniform sampler2D ShadowMap;
uniform vec3 LightPos;
uniform int LightType;
uniform int UseShadows;
uniform int UsePointShadowAtlas;
uniform float ShadowBias;
uniform float ShadowDarkness;
uniform vec2 ShadowMapSize;
uniform mat4 PointShadowMvp0;
uniform mat4 PointShadowMvp1;
uniform mat4 PointShadowMvp2;
uniform mat4 PointShadowMvp3;
uniform mat4 PointShadowMvp4;
uniform mat4 PointShadowMvp5;
in vec3 v_pos;
in vec4 v_shadow_pos;
out vec4 f_color;

float sample_shadow_factor(vec4 shadow_pos) {
    if (UseShadows == 0) {
        return 1.0;
    }
    vec2 atlas_base = vec2(0.0);
    vec2 atlas_scale = vec2(1.0);
    vec4 sample_pos = shadow_pos;
    if (UsePointShadowAtlas == 1 && LightType == 1) {
        vec3 rel = v_pos - LightPos;
        vec3 a = abs(rel);
        mat4 face_mvp = PointShadowMvp0;
        if (a.x >= a.y && a.x >= a.z) {
            if (rel.x >= 0.0) {
                face_mvp = PointShadowMvp0;
                atlas_base = vec2(0.0, 0.0);
            } else {
                face_mvp = PointShadowMvp1;
                atlas_base = vec2(0.33333334, 0.0);
            }
        } else if (a.y >= a.z) {
            if (rel.y >= 0.0) {
                face_mvp = PointShadowMvp2;
                atlas_base = vec2(0.6666667, 0.0);
            } else {
                face_mvp = PointShadowMvp3;
                atlas_base = vec2(0.0, 0.5);
            }
        } else {
            if (rel.z >= 0.0) {
                face_mvp = PointShadowMvp4;
                atlas_base = vec2(0.33333334, 0.5);
            } else {
                face_mvp = PointShadowMvp5;
                atlas_base = vec2(0.6666667, 0.5);
            }
        }
        atlas_scale = vec2(0.33333334, 0.5);
        sample_pos = face_mvp * vec4(v_pos, 1.0);
    }
    float w = sample_pos.w;
    if (abs(w) <= 1e-6) {
        return 1.0;
    }
    vec3 proj = sample_pos.xyz / w;
    vec3 uvw = proj * 0.5 + 0.5;
    if (uvw.x < 0.0 || uvw.x > 1.0 || uvw.y < 0.0 || uvw.y > 1.0 || uvw.z < 0.0 || uvw.z > 1.0) {
        return 1.0;
    }
    vec2 texel = 1.0 / max(ShadowMapSize, vec2(1.0));
    float visible = 0.0;
    float sample_count = 0.0;
    int radius = (LightType == 3) ? 2 : 1;
    for (int x = -2; x <= 2; x++) {
        for (int y = -2; y <= 2; y++) {
            if (abs(x) > radius || abs(y) > radius) {
                continue;
            }
            vec2 sample_uv = atlas_base + uvw.xy * atlas_scale + vec2(float(x), float(y)) * texel;
            if (UsePointShadowAtlas == 1 && LightType == 1) {
                vec2 tile_min = atlas_base + texel * 0.5;
                vec2 tile_max = atlas_base + atlas_scale - texel * 0.5;
                sample_uv = clamp(sample_uv, tile_min, tile_max);
            }
            float closest = texture(ShadowMap, sample_uv).r;
            visible += ((uvw.z - ShadowBias) <= closest) ? 1.0 : 0.0;
            sample_count += 1.0;
        }
    }
    visible /= max(sample_count, 1.0);
    return mix(1.0 - clamp(ShadowDarkness, 0.0, 1.0), 1.0, visible);
}

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
    rgb *= sample_shadow_factor(v_shadow_pos);
    vec2 rel = v_pos.xz - FadeOrigin;
    float dist = length(rel);
    float fade = 1.0;
    if (FadeEnd > FadeStart) {
        fade = 1.0 - smoothstep(FadeStart, FadeEnd, dist);
    }
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

    "overlay_point_vertex": """
#version 330
uniform mat4 Mvp;
uniform float PointSize;
in vec3 in_position;
in vec4 in_color;
out vec4 v_color;
void main() {
    gl_Position = Mvp * vec4(in_position, 1.0);
    gl_PointSize = PointSize;
    v_color = in_color;
}
""",

    "overlay_point_fragment": """
#version 330
in vec4 v_color;
out vec4 f_color;
void main() {
    vec2 p = gl_PointCoord * 2.0 - 1.0;
    float r2 = dot(p, p);
    if (r2 > 1.0) discard;
    float edge = 1.0 - smoothstep(0.72, 1.0, r2);
    f_color = vec4(v_color.rgb, v_color.a * edge);
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
uniform mat4 LightMvp;
uniform float SplatWorldScale;

in vec2 in_corner;
in vec3 in_pos;
in vec4 in_col;
in float in_rad;
in vec3 in_scale3;
in vec4 in_rot;
in float in_lit;
in float in_glow;

out vec2 v_uv;
out vec4 v_col;
out vec3 v_world_norm;
out vec3 v_world_pos;
out vec4 v_shadow_pos;
out float v_lit;
out float v_glow;

vec3 quat_rotate(vec3 v, vec4 q) {
    vec3 t = 2.0 * cross(q.xyz, v);
    return v + q.w * t + cross(q.xyz, t);
}

void main() {
    vec4 world_p = Model * vec4(in_pos, 1.0);
    vec4 view_p = View * world_p;

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
    v_world_norm = normalize(mat3(Model) * quat_rotate(vec3(0.0, 0.0, 1.0), in_rot));
    v_world_pos = world_p.xyz;
    v_shadow_pos = LightMvp * world_p;
    v_lit = in_lit;
    v_glow = in_glow;

    gl_Position = Proj * view_p;
}
""",

    "splatq_fragment": """
#version 330
uniform vec3 LightDir;
uniform vec3 LightPos;
uniform int LightType;
uniform float LightIntensity;
uniform float AmbientLight;
uniform float LightRange;
uniform int UsePointShadowAtlas;
uniform mat4 PointShadowMvp0;
uniform mat4 PointShadowMvp1;
uniform mat4 PointShadowMvp2;
uniform mat4 PointShadowMvp3;
uniform mat4 PointShadowMvp4;
uniform mat4 PointShadowMvp5;
uniform float SpotCosInner;
uniform float SpotCosOuter;
uniform sampler2D ShadowMap;
uniform int UseSplatLighting;
uniform int UseShadows;
uniform float ShadowBias;
uniform float ShadowDarkness;
uniform vec2 ShadowMapSize;
in vec2 v_uv;
in vec4 v_col;
in vec3 v_world_norm;
in vec3 v_world_pos;
in vec4 v_shadow_pos;
in float v_lit;
in float v_glow;
out vec4 f_color;

vec3 safe_normalize(vec3 v) {
    float len2 = dot(v, v);
    if (len2 <= 1e-10) {
        return vec3(0.0, 0.0, 1.0);
    }
    return v * inversesqrt(len2);
}

float sample_shadow_factor(vec4 shadow_pos, vec3 normal, vec3 light_vec) {
    if (UseShadows == 0) {
        return 1.0;
    }
    vec2 atlas_base = vec2(0.0);
    vec2 atlas_scale = vec2(1.0);
    vec4 sample_pos = shadow_pos;
    if (UsePointShadowAtlas == 1 && LightType == 1) {
        vec3 rel = v_world_pos - LightPos;
        vec3 a = abs(rel);
        mat4 face_mvp = PointShadowMvp0;
        if (a.x >= a.y && a.x >= a.z) {
            if (rel.x >= 0.0) {
                face_mvp = PointShadowMvp0;
                atlas_base = vec2(0.0, 0.0);
            } else {
                face_mvp = PointShadowMvp1;
                atlas_base = vec2(0.33333334, 0.0);
            }
        } else if (a.y >= a.z) {
            if (rel.y >= 0.0) {
                face_mvp = PointShadowMvp2;
                atlas_base = vec2(0.6666667, 0.0);
            } else {
                face_mvp = PointShadowMvp3;
                atlas_base = vec2(0.0, 0.5);
            }
        } else {
            if (rel.z >= 0.0) {
                face_mvp = PointShadowMvp4;
                atlas_base = vec2(0.33333334, 0.5);
            } else {
                face_mvp = PointShadowMvp5;
                atlas_base = vec2(0.6666667, 0.5);
            }
        }
        atlas_scale = vec2(0.33333334, 0.5);
        sample_pos = face_mvp * vec4(v_world_pos, 1.0);
    }
    float w = sample_pos.w;
    if (abs(w) <= 1e-6) {
        return 1.0;
    }
    vec3 proj = sample_pos.xyz / w;
    vec3 uvw = proj * 0.5 + 0.5;
    if (uvw.x < 0.0 || uvw.x > 1.0 || uvw.y < 0.0 || uvw.y > 1.0 || uvw.z < 0.0 || uvw.z > 1.0) {
        return 1.0;
    }
    vec3 n = safe_normalize(normal);
    vec3 l = safe_normalize(light_vec);
    float ndotl = clamp(abs(dot(n, l)), 0.0, 1.0);
    float bias = max(ShadowBias * (1.0 - ndotl), ShadowBias * 0.25);
    vec2 texel = 1.0 / max(ShadowMapSize, vec2(1.0));
    float visible = 0.0;
    float sample_count = 0.0;
    int radius = (LightType == 3) ? 2 : 1;
    for (int x = -2; x <= 2; x++) {
        for (int y = -2; y <= 2; y++) {
            if (abs(x) > radius || abs(y) > radius) {
                continue;
            }
            vec2 sample_uv = atlas_base + uvw.xy * atlas_scale + vec2(float(x), float(y)) * texel;
            if (UsePointShadowAtlas == 1 && LightType == 1) {
                vec2 tile_min = atlas_base + texel * 0.5;
                vec2 tile_max = atlas_base + atlas_scale - texel * 0.5;
                sample_uv = clamp(sample_uv, tile_min, tile_max);
            }
            float closest = texture(ShadowMap, sample_uv).r;
            visible += ((uvw.z - bias) <= closest) ? 1.0 : 0.0;
            sample_count += 1.0;
        }
    }
    visible /= max(sample_count, 1.0);
    return mix(1.0 - clamp(ShadowDarkness, 0.0, 1.0), 1.0, visible);
}

vec3 light_vector_at(vec3 world_pos, out float attenuation) {
    attenuation = 1.0;
    if (LightType == 0) {
        return safe_normalize(LightDir);
    }
    vec3 to_light = LightPos - world_pos;
    float dist = length(to_light);
    vec3 l = (dist > 1e-5) ? (to_light / dist) : safe_normalize(LightDir);
    float range = max(LightRange, 0.001);
    float d = dist / range;
    attenuation = 1.0 / (1.0 + 4.0 * d * d);
    if (LightType == 2) {
        float cone = dot(l, safe_normalize(LightDir));
        attenuation *= smoothstep(SpotCosOuter, SpotCosInner, cone);
    } else if (LightType == 3) {
        attenuation *= 0.85;
    }
    return l;
}

void main() {
    float r2 = dot(v_uv, v_uv);
    if (r2 > 1.0) discard;

    float a = exp(-r2 * 2.0);
    a *= v_col.a;

    if (a < 1e-4) discard;

    vec3 base_rgb = v_col.rgb;
    vec3 rgb = base_rgb;
    if (UseSplatLighting == 1 && v_lit > 0.5) {
        vec3 n = safe_normalize(v_world_norm);
        float light_attenuation = 1.0;
        vec3 l = light_vector_at(v_world_pos, light_attenuation);
        float diffuse = clamp(abs(dot(n, l)), 0.0, 1.0) * light_attenuation;
        float shadow_visibility = sample_shadow_factor(v_shadow_pos, n, l);
        float lum = clamp(AmbientLight, 0.0, 1.0) + diffuse * 0.72 * max(LightIntensity, 0.0) * shadow_visibility;
        rgb *= clamp(lum, 0.0, 3.0);
    }
    float glow = clamp(v_glow, 0.0, 4.0);
    if (glow > 1e-4) {
        vec3 emissive = base_rgb * (0.75 + 1.35 * glow) + vec3(0.12, 0.08, 0.03) * glow;
        rgb = mix(rgb, emissive, clamp(glow * 0.72, 0.0, 1.0));
        a = min(1.0, a * (1.0 + 0.35 * glow));
    }

    f_color = vec4(rgb * a, a);
}
""",

    "splat_shadow_vertex": """
#version 330

uniform mat4 LightProj;
uniform mat4 LightView;
uniform float SplatWorldScale;

in vec2 in_corner;
in vec3 in_pos;
in vec4 in_col;
in float in_rad;
in vec3 in_scale3;
in vec4 in_rot;

out vec2 v_uv;
out float v_alpha;

vec3 quat_rotate(vec3 v, vec4 q) {
    vec3 t = 2.0 * cross(q.xyz, v);
    return v + q.w * t + cross(q.xyz, t);
}

void main() {
    vec4 view_p = LightView * vec4(in_pos, 1.0);

    float s = in_rad * SplatWorldScale;
    mat3 V = mat3(LightView);

    vec3 ax = V * quat_rotate(vec3(1.0, 0.0, 0.0), in_rot) * (in_scale3.x * s);
    vec3 ay = V * quat_rotate(vec3(0.0, 1.0, 0.0), in_rot) * (in_scale3.y * s);
    vec3 az = V * quat_rotate(vec3(0.0, 0.0, 1.0), in_rot) * (in_scale3.z * s);

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
    v_alpha = in_col.a;

    gl_Position = LightProj * view_p;
}
""",

    "splat_shadow_fragment": """
#version 330
uniform float ShadowCasterId;
in vec2 v_uv;
in float v_alpha;
layout(location = 0) out float f_shadow_id;

void main() {
    float r2 = dot(v_uv, v_uv);
    if (r2 > 1.0) discard;
    float a = exp(-r2 * 2.0) * v_alpha;
    if (a < 0.08) discard;
    f_shadow_id = ShadowCasterId;
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
