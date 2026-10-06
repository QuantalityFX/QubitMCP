"""Extend the mesh/splat GLSL with an independent second light and shadow map.

Reuse the same sampling code for both lights, including point-light atlas and
self-shadow handling. Fixed sampler names are compatible with GLSL 330.
"""
import re


def with_second_light(source: str, *, mesh: bool) -> str:
    uniforms = {
        "LightDir", "LightPos", "LightType", "LightIntensity", "LightRange",
        "UsePointShadowAtlas", "SpotCosInner", "SpotCosOuter", "ShadowMap",
        "ShadowIdMap", "UseShadows", "UseSelfShadows", "ShadowBias",
        "ShadowDarkness", "ShadowMapSize", "AreaAxisU", "AreaAxisV",
        "AreaSize", "UseAreaShadowAtlas",
        *(f"PointShadowMvp{i}" for i in range(6)),
        *(f"AreaShadowMvp{i}" for i in range(5)),
    }
    renamed = uniforms | {
        "sample_shadow_factor", "sample_area_shadow_factor",
        "area_sample_offset", "area_shadow_base", "area_shadow_mvp",
        "area_light_diffuse", "light_vector_at",
    }

    def rename(text):
        return re.sub(r"\b\w+\b", lambda m: m[0] + "2" if m[0] in renamed else m[0], text)

    declarations = re.findall(r"uniform\s+\w+\s+(\w+)\s*;", source)
    second_uniforms = "\n".join(
        rename(re.search(r"uniform\s+\w+\s+" + name + r"\s*;", source)[0])
        for name in declarations if name in uniforms
    )
    start = source.index("float sample_shadow_factor(")
    end = source.index("vec4 composite_over(" if mesh else "void main()", start)
    helpers = rename(source[start:end])
    insertion = (
        "uniform int UseSecondLight;\nuniform mat4 LightMvp2;\n"
        + second_uniforms + "\n" + helpers
    )
    source = source.replace("void main()", insertion + "\nvoid main()", 1)
    if mesh:
        extra = """
        if (UseSecondLight == 1) {
            vec3 n2 = shading_n;
            if (LightType2 != 0 && !gl_FrontFacing) n2 = -n2;
            float attenuation2;
            vec3 l2 = light_vector_at2(v_world_pos, attenuation2);
            float gate2 = (LightType2 == 0 && !gl_FrontFacing) ? 0.0 : 1.0;
            vec4 shadow_pos2 = LightMvp2 * vec4(v_world_pos, 1.0);
            float diffuse2 = max(dot(n2, l2), 0.0) * gate2 * attenuation2;
            float visibility2 = sample_shadow_factor2(shadow_pos2, n2, l2);
            if (LightType2 == 3) {
                diffuse2 = area_light_diffuse2(v_world_pos, shadow_pos2, n2);
                visibility2 = 1.0;
            }
            direct += diffuse2 * 0.86 * max(LightIntensity2, 0.0) * visibility2;
        }
"""
        source = source.replace("        lum = ambient + direct;", extra + "        lum = ambient + direct;", 1)
    else:
        extra = """
        if (UseSecondLight == 1) {
            float attenuation2;
            vec3 l2 = light_vector_at2(v_world_pos, attenuation2);
            vec4 shadow_pos2 = LightMvp2 * vec4(v_world_pos, 1.0);
            float diffuse2 = clamp(abs(dot(n, l2)), 0.0, 1.0) * attenuation2;
            float visibility2 = sample_shadow_factor2(shadow_pos2, n, l2);
            if (LightType2 == 3) {
                diffuse2 = area_light_diffuse2(v_world_pos, shadow_pos2, n);
                visibility2 = 1.0;
            }
            lum += diffuse2 * 0.72 * max(LightIntensity2, 0.0) * visibility2;
        }
"""
        source = source.replace("        rgb *= clamp(lum, 0.0, 3.0);", extra + "        rgb *= clamp(lum, 0.0, 3.0);", 1)
    return source
