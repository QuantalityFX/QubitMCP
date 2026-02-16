#include "MaterialGeneratorBuilder.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetToolsModule.h"
#include "Modules/ModuleManager.h"
#include "Factories/MaterialFactoryNew.h"
#include "Factories/MaterialInstanceConstantFactoryNew.h"
#include "MaterialEditingLibrary.h"
#include "Engine/Texture.h"
#include "Materials/Material.h"
#include "Materials/MaterialExpressionAdd.h"
#include "Materials/MaterialExpressionAppendVector.h"
#include "Materials/MaterialExpressionComponentMask.h"
#include "Materials/MaterialExpressionConstant.h"
#include "Materials/MaterialExpressionCustom.h"
#include "Materials/MaterialExpressionMultiply.h"
#include "Materials/MaterialExpressionScalarParameter.h"
#include "Materials/MaterialExpressionTextureCoordinate.h"
#include "Materials/MaterialExpressionTextureObjectParameter.h"
#include "Materials/MaterialExpressionTime.h"
#include "Materials/MaterialExpressionVectorParameter.h"
#include "Materials/MaterialInstanceConstant.h"
#include "Misc/PackageName.h"
#include "Runtime/Launch/Resources/Version.h"

namespace MaterialGenerator
{
    static const TCHAR* MatrixRainCustomCode = TEXT(R"MGCODE(
#define HASH11(n) frac(sin((n))*43758.5453123)
#define HASH21(p) frac(sin(dot((p),float2(127.1,311.7)))*43758.5453123)
#define SMOOTHH(a,b,x) smoothstep((a),(b),(x))

float base = 20.0;
float tiling = max(1.0, Tiling);
float pack_x = max(1.0, PackX);
float pack_y = max(1.0, PackY);
float seed = frac(SeedIn * 0.000244140625);
float raw_t = ProcTime;
float cols = max(1.0, tiling * pack_x * base);
float rows = max(1.0, tiling * pack_y * base);
float2 shift = CellOffset / max(float2(cols, rows), float2(1.0, 1.0));
float2 p = frac(UV + shift) * float2(cols, rows);

float dir_id = Direction;
float horiz = step(1.5, dir_id);
float neg = step(0.5, fmod(dir_id, 2.0));
float dir = lerp(1.0, -1.0, neg);

float stream_id = lerp(floor(p.x), floor(p.y), horiz);
float along = lerp(p.y, p.x, horiz);
float along_cells = lerp(rows, cols, horiz);

float speed = lerp(0.6, 1.8, HASH11(stream_id * 0.73 + seed * 91.7));
float trail = lerp(6.0, 18.0, HASH11(stream_id * 1.31 + seed * 57.3));
float col_phase = HASH11(stream_id * 1.19 + seed * 53.1) * along_cells;
float life_lo = max(0.1, LifeMin);
float life_hi = max(life_lo, LifeMax);
float life = lerp(life_lo, life_hi, HASH11(stream_id * 1.61 + seed * 9.7));
trail *= lerp(0.8, 1.6, life / 10.0);
trail = clamp(trail, 6.0, 26.0);

float fade = lerp(1.1025, 2.646, HASH11(stream_id * 2.11 + seed * 7.3));
float dmin = min(ChainMin, ChainMax);
float dmax = max(ChainMin, ChainMax);
float dead = lerp(dmin, dmax, HASH11(stream_id * 2.71 + seed * 5.1));
float cycle = life + fade + dead;
float offset = HASH11(stream_id * 3.17 + seed * 17.1) * cycle;
float tcol = raw_t + offset;
float base_cycle = floor(tcol / cycle) * cycle;
float t_in = tcol - base_cycle;

float3 bg_rgb = float3(0.01, 0.03, 0.01);
float bg_alpha = 1.0;
if (BgEnabled > 0.5) bg_rgb = Bg;

float t_alive = min(t_in, life);
float travel = t_alive * max(0.0, AnimSpeed) * max(0.0, speed);
float grow = saturate(travel / max(trail, 1.0));
float trail_len = min(trail, max(1.0, travel + 1.0));

float fade_out = 1.0;
if (t_in >= life)
{
    float fade_t = (t_in - life) / max(0.0001, fade);
    fade_out = 1.0 - saturate(fade_t);
}

float row_seed = floor(along);
float stick_seed = HASH21(float2(stream_id * 0.37 + seed * 17.0, row_seed * 0.73 + seed * 3.0));
float stick = step(stick_seed, 0.18);
float stick_rand = HASH11(stream_id * 7.13 + row_seed * 2.13 + seed * 3.7);
float stick_len = trail * lerp(0.4, 1.5, stick_rand);
trail_len = min(trail_len + stick * stick_len * grow, along_cells * 0.9);

float after_fade = 0.0;
if (t_in > life + fade && stick > 0.5)
{
    float after_t = (t_in - (life + fade)) / max(0.0001, dead);
    after_fade = 1.0 - saturate(after_t);
}
float live = max(fade_out, after_fade * stick);

float motion_t = (base_cycle + t_alive - offset) * max(0.0, AnimSpeed);
float scroll = motion_t * speed * dir + col_phase;
if (PanEnabled < 0.5) scroll = floor(scroll + 0.0001);

float stream_pos = along - scroll;
stream_pos = lerp(stream_pos, along, stick);
float row = floor(stream_pos);

float2 f = frac(p);
if (horiz > 0.5)
{
    if (dir > 0.0) f = float2(f.y, 1.0 - f.x);
    else f = float2(1.0 - f.y, f.x);
}

float head = fmod(scroll, along_cells);
if (head < 0.0) head += along_cells;
float dy = (dir > 0.0) ? (head - along) : (along - head);
if (dy < 0.0) dy += along_cells;

float t = 1.0 - dy / max(trail_len, 1.0);
if (t <= 0.0) return float4(bg_rgb, bg_alpha);

float2 pg = max(GlyphGrid, float2(1.0, 1.0));
float glyph_count = max(1.0, min(pg.x * pg.y, GlyphCount));
float hold = lerp(0.3, 3.0, HASH11(stream_id * 1.91 + row * 0.73 + seed * 11.0));
hold *= lerp(1.0, 2.8, stick);
float glyph_anim = floor(motion_t / max(0.05, hold));

float2 gh = float2(
    stream_id + row * 0.11 + glyph_anim * 0.07 + seed * 37.0,
    row + stream_id * 0.19 + glyph_anim * 0.13 + seed * 61.0
);
float glyph_idx = floor(HASH21(gh) * glyph_count);
float2 gcell = float2(fmod(glyph_idx, pg.x), floor(glyph_idx / pg.x));

float mirror = step(HASH21(float2(stream_id * 0.71 + seed * 19.0, row * 1.37 + seed * 3.0)), 0.22);
if (mirror > 0.5) f.x = 1.0 - f.x;

float2 pad = float2(0.08, 0.08);
float2 glyph_uv = (gcell + lerp(pad, 1.0 - pad, f)) / pg;
float glyph = Texture2DSampleLevel(GlyphAtlas, GlyphAtlasSampler, glyph_uv, 0.0).r;

// Avoid derivatives so ray tracing and other SM6 permutations compile cleanly.
float edge = lerp(0.008, 0.030, saturate(Softness));
glyph = SMOOTHH(0.20 - edge, 0.86 + edge, glyph);

float3 head_col = float3(0.78, 0.96, 0.82);
float3 tail_col = float3(0.11, 0.78, 0.14);
float3 color = lerp(tail_col, head_col, SMOOTHH(0.85, 0.98, t));

float t_fade = t * live;
float3 rgb = lerp(bg_rgb, color, glyph * t_fade);
float alpha = lerp(bg_alpha, 1.0, glyph * t_fade);

return float4(rgb, alpha);
#undef HASH11
#undef HASH21
#undef SMOOTHH
)MGCODE");

    FString NormalizePackagePath(const FString& InPath)
    {
        FString Path = InPath.TrimStartAndEnd();
        if (Path.IsEmpty())
        {
            Path = TEXT("Materials");
        }

        Path.ReplaceInline(TEXT("\\"), TEXT("/"));

        // Accept disk paths and convert ".../Content/..." to "/Game/...".
        const int32 ContentToken = Path.Find(TEXT("/Content/"), ESearchCase::IgnoreCase, ESearchDir::FromStart);
        if (ContentToken != INDEX_NONE)
        {
            const FString Relative = Path.Mid(ContentToken + 9).TrimStartAndEnd();
            Path = Relative.IsEmpty() ? TEXT("/Game") : FString::Printf(TEXT("/Game/%s"), *Relative);
        }
        else if (Path.EndsWith(TEXT("/Content"), ESearchCase::IgnoreCase))
        {
            Path = TEXT("/Game");
        }

        // Accept paths that begin with Content/ directly.
        if (Path.StartsWith(TEXT("Content/"), ESearchCase::IgnoreCase))
        {
            Path = Path.Mid(8);
        }
        else if (Path.Equals(TEXT("Content"), ESearchCase::IgnoreCase))
        {
            Path.Empty();
        }
        else if (Path.StartsWith(TEXT("/Content/"), ESearchCase::IgnoreCase))
        {
            Path = Path.Mid(9);
        }
        else if (Path.Equals(TEXT("/Content"), ESearchCase::IgnoreCase))
        {
            Path.Empty();
        }

        if (!Path.StartsWith(TEXT("/Game"), ESearchCase::IgnoreCase))
        {
            if (Path == TEXT("/"))
            {
                Path = TEXT("/Game");
            }
            else if (Path.IsEmpty())
            {
                Path = TEXT("/Game");
            }
            else if (Path.StartsWith(TEXT("/")))
            {
                Path = FString::Printf(TEXT("/Game%s"), *Path);
            }
            else
            {
                Path = FString::Printf(TEXT("/Game/%s"), *Path);
            }
        }

        while (Path.Contains(TEXT("//")))
        {
            Path.ReplaceInline(TEXT("//"), TEXT("/"));
        }

        while (Path.Len() > 1 && Path.EndsWith(TEXT("/")))
        {
            Path.LeftChopInline(1, EAllowShrinking::No);
        }

        return Path;
    }

    bool IsSimpleAssetName(const FString& Name)
    {
        return !Name.IsEmpty()
            && !Name.Contains(TEXT("/"))
            && !Name.Contains(TEXT("\\"))
            && !Name.Contains(TEXT("."));
    }

    template<typename TExpression>
    TExpression* CreateExpression(UMaterial* Material, int32 X, int32 Y)
    {
        return Cast<TExpression>(UMaterialEditingLibrary::CreateMaterialExpression(Material, TExpression::StaticClass(), X, Y));
    }

    UMaterialExpressionScalarParameter* CreateScalarParameter(UMaterial* Material, const TCHAR* Name, float DefaultValue, int32 X, int32 Y)
    {
        UMaterialExpressionScalarParameter* Node = CreateExpression<UMaterialExpressionScalarParameter>(Material, X, Y);
        if (Node)
        {
            Node->ParameterName = FName(Name);
            Node->DefaultValue = DefaultValue;
        }
        return Node;
    }

    UMaterialExpressionVectorParameter* CreateVectorParameter(UMaterial* Material, const TCHAR* Name, const FLinearColor& DefaultValue, int32 X, int32 Y)
    {
        UMaterialExpressionVectorParameter* Node = CreateExpression<UMaterialExpressionVectorParameter>(Material, X, Y);
        if (Node)
        {
            Node->ParameterName = FName(Name);
            Node->DefaultValue = DefaultValue;
        }
        return Node;
    }

    template<typename TAsset, typename TFactory>
    TAsset* LoadOrCreateAsset(const FString& PackagePath, const FString& AssetName, bool& bOutCreated)
    {
        bOutCreated = false;

        const FString ObjectPath = FString::Printf(TEXT("%s/%s.%s"), *PackagePath, *AssetName, *AssetName);
        if (TAsset* Existing = LoadObject<TAsset>(nullptr, *ObjectPath))
        {
            return Existing;
        }

        FAssetToolsModule& AssetToolsModule = FModuleManager::LoadModuleChecked<FAssetToolsModule>("AssetTools");
        TFactory* Factory = NewObject<TFactory>();
        UObject* CreatedAsset = AssetToolsModule.Get().CreateAsset(AssetName, PackagePath, TAsset::StaticClass(), Factory);
        TAsset* TypedAsset = Cast<TAsset>(CreatedAsset);
        bOutCreated = (TypedAsset != nullptr);
        return TypedAsset;
    }

    void AddCustomInput(UMaterialExpressionCustom* Custom, const TCHAR* Name, UMaterialExpression* Expression)
    {
        FCustomInput& Input = Custom->Inputs.AddDefaulted_GetRef();
        Input.InputName = Name;
        Input.Input.Expression = Expression;
        Input.Input.OutputIndex = 0;
    }
}

FMaterialGeneratorResult FMaterialGeneratorBuilder::CreateMatrixRainMaterial(const FMaterialGeneratorConfig& Config)
{
    FMaterialGeneratorResult Result;

    const FString PackagePath = MaterialGenerator::NormalizePackagePath(Config.PackagePath);
    const FString MasterName = Config.MasterMaterialName.TrimStartAndEnd();
    const FString InstanceName = Config.MaterialInstanceName.TrimStartAndEnd();

    if (!FPackageName::IsValidLongPackageName(PackagePath))
    {
        Result.Message = FString::Printf(TEXT("Invalid package path '%s'."), *PackagePath);
        return Result;
    }

    if (!MaterialGenerator::IsSimpleAssetName(MasterName))
    {
        Result.Message = TEXT("Invalid master material name. Use a simple asset name (no slashes or dots).");
        return Result;
    }

    if (Config.bCreateMaterialInstance && !MaterialGenerator::IsSimpleAssetName(InstanceName))
    {
        Result.Message = TEXT("Invalid material instance name. Use a simple asset name (no slashes or dots).");
        return Result;
    }

    bool bCreatedMaster = false;
    UMaterial* MasterMaterial = MaterialGenerator::LoadOrCreateAsset<UMaterial, UMaterialFactoryNew>(PackagePath, MasterName, bCreatedMaster);
    if (!MasterMaterial)
    {
        Result.Message = TEXT("Failed to create or load the master material asset.");
        return Result;
    }

    UTexture* GlyphTexture = nullptr;
    const FString GlyphPath = Config.GlyphTextureObjectPath.TrimStartAndEnd();
    if (!GlyphPath.IsEmpty())
    {
        GlyphTexture = LoadObject<UTexture>(nullptr, *GlyphPath);
    }

    MasterMaterial->Modify();

    UMaterialEditingLibrary::DeleteAllMaterialExpressions(MasterMaterial);

#if ENGINE_MAJOR_VERSION >= 5
    MasterMaterial->SetShadingModel(MSM_Unlit);
#else
    MasterMaterial->ShadingModel = MSM_Unlit;
#endif
    MasterMaterial->BlendMode = BLEND_Translucent;
    MasterMaterial->TwoSided = true;

    UMaterialExpressionTextureCoordinate* UVNode = MaterialGenerator::CreateExpression<UMaterialExpressionTextureCoordinate>(MasterMaterial, -1700, -900);
    UMaterialExpressionTime* TimeNode = MaterialGenerator::CreateExpression<UMaterialExpressionTime>(MasterMaterial, -1700, -760);

    UMaterialExpressionScalarParameter* TilingNode = MaterialGenerator::CreateScalarParameter(MasterMaterial, TEXT("Tiling"), 1.0f, -1450, -650);
    UMaterialExpressionScalarParameter* PackXNode = MaterialGenerator::CreateScalarParameter(MasterMaterial, TEXT("PackX"), 1.0f, -1450, -540);
    UMaterialExpressionScalarParameter* PackYNode = MaterialGenerator::CreateScalarParameter(MasterMaterial, TEXT("PackY"), 1.0f, -1450, -430);
    UMaterialExpressionScalarParameter* DirectionNode = MaterialGenerator::CreateScalarParameter(MasterMaterial, TEXT("Direction"), 0.0f, -1450, -320);
    UMaterialExpressionScalarParameter* SeedNode = MaterialGenerator::CreateScalarParameter(MasterMaterial, TEXT("SeedIn"), 1337.0f, -1450, -210);
    UMaterialExpressionScalarParameter* AnimSpeedNode = MaterialGenerator::CreateScalarParameter(MasterMaterial, TEXT("AnimSpeed"), 1.0f, -1450, -100);
    UMaterialExpressionVectorParameter* BgNode = MaterialGenerator::CreateVectorParameter(MasterMaterial, TEXT("Bg"), FLinearColor(0.01f, 0.03f, 0.01f, 1.0f), -1450, 10);
    UMaterialExpressionScalarParameter* BgEnabledNode = MaterialGenerator::CreateScalarParameter(MasterMaterial, TEXT("BgEnabled"), 1.0f, -1450, 120);
    UMaterialExpressionScalarParameter* SoftnessNode = MaterialGenerator::CreateScalarParameter(MasterMaterial, TEXT("Softness"), 0.35f, -1450, 230);
    UMaterialExpressionScalarParameter* PanEnabledNode = MaterialGenerator::CreateScalarParameter(MasterMaterial, TEXT("PanEnabled"), 0.0f, -1450, 560);
    UMaterialExpressionScalarParameter* LifeMinNode = MaterialGenerator::CreateScalarParameter(MasterMaterial, TEXT("LifeMin"), 3.0f, -1450, 670);
    UMaterialExpressionScalarParameter* LifeMaxNode = MaterialGenerator::CreateScalarParameter(MasterMaterial, TEXT("LifeMax"), 14.0f, -1450, 780);
    UMaterialExpressionScalarParameter* ChainMinNode = MaterialGenerator::CreateScalarParameter(MasterMaterial, TEXT("ChainMin"), 0.8f, -1450, 890);
    UMaterialExpressionScalarParameter* ChainMaxNode = MaterialGenerator::CreateScalarParameter(MasterMaterial, TEXT("ChainMax"), 2.2f, -1450, 1000);
    UMaterialExpressionScalarParameter* GlyphCountNode = MaterialGenerator::CreateScalarParameter(MasterMaterial, TEXT("GlyphCount"), 96.0f, -1450, 1110);

    UMaterialExpressionScalarParameter* OffsetXNode = MaterialGenerator::CreateScalarParameter(MasterMaterial, TEXT("OffsetX"), 0.0f, -1180, 340);
    UMaterialExpressionScalarParameter* OffsetYNode = MaterialGenerator::CreateScalarParameter(MasterMaterial, TEXT("OffsetY"), 0.0f, -1180, 450);

    UMaterialExpressionScalarParameter* GlyphGridXNode = MaterialGenerator::CreateScalarParameter(MasterMaterial, TEXT("GlyphGridX"), 16.0f, -1180, 1220);
    UMaterialExpressionScalarParameter* GlyphGridYNode = MaterialGenerator::CreateScalarParameter(MasterMaterial, TEXT("GlyphGridY"), 6.0f, -1180, 1330);

    UMaterialExpressionAppendVector* OffsetAppendNode = MaterialGenerator::CreateExpression<UMaterialExpressionAppendVector>(MasterMaterial, -920, 400);
    UMaterialExpressionAppendVector* GlyphGridAppendNode = MaterialGenerator::CreateExpression<UMaterialExpressionAppendVector>(MasterMaterial, -920, 1270);

    if (OffsetAppendNode)
    {
        OffsetAppendNode->A.Expression = OffsetXNode;
        OffsetAppendNode->B.Expression = OffsetYNode;
    }

    if (GlyphGridAppendNode)
    {
        GlyphGridAppendNode->A.Expression = GlyphGridXNode;
        GlyphGridAppendNode->B.Expression = GlyphGridYNode;
    }

    UMaterialExpressionTextureObjectParameter* GlyphAtlasNode = MaterialGenerator::CreateExpression<UMaterialExpressionTextureObjectParameter>(MasterMaterial, -1450, 1440);
    if (GlyphAtlasNode)
    {
        GlyphAtlasNode->ParameterName = FName(TEXT("GlyphAtlas"));
        GlyphAtlasNode->Texture = GlyphTexture;
    }

    UMaterialExpressionCustom* CustomNode = MaterialGenerator::CreateExpression<UMaterialExpressionCustom>(MasterMaterial, -420, 250);
    if (!CustomNode)
    {
        Result.Message = TEXT("Failed to create material custom node.");
        return Result;
    }

    CustomNode->OutputType = CMOT_Float4;
    CustomNode->Description = TEXT("Generated Matrix Rain shader");
    CustomNode->Code = MaterialGenerator::MatrixRainCustomCode;
    CustomNode->Inputs.Empty();

    MaterialGenerator::AddCustomInput(CustomNode, TEXT("UV"), UVNode);
    MaterialGenerator::AddCustomInput(CustomNode, TEXT("ProcTime"), TimeNode);
    MaterialGenerator::AddCustomInput(CustomNode, TEXT("Tiling"), TilingNode);
    MaterialGenerator::AddCustomInput(CustomNode, TEXT("PackX"), PackXNode);
    MaterialGenerator::AddCustomInput(CustomNode, TEXT("PackY"), PackYNode);
    MaterialGenerator::AddCustomInput(CustomNode, TEXT("Direction"), DirectionNode);
    MaterialGenerator::AddCustomInput(CustomNode, TEXT("SeedIn"), SeedNode);
    MaterialGenerator::AddCustomInput(CustomNode, TEXT("AnimSpeed"), AnimSpeedNode);
    MaterialGenerator::AddCustomInput(CustomNode, TEXT("Bg"), BgNode);
    MaterialGenerator::AddCustomInput(CustomNode, TEXT("BgEnabled"), BgEnabledNode);
    MaterialGenerator::AddCustomInput(CustomNode, TEXT("Softness"), SoftnessNode);
    MaterialGenerator::AddCustomInput(CustomNode, TEXT("CellOffset"), OffsetAppendNode);
    MaterialGenerator::AddCustomInput(CustomNode, TEXT("PanEnabled"), PanEnabledNode);
    MaterialGenerator::AddCustomInput(CustomNode, TEXT("LifeMin"), LifeMinNode);
    MaterialGenerator::AddCustomInput(CustomNode, TEXT("LifeMax"), LifeMaxNode);
    MaterialGenerator::AddCustomInput(CustomNode, TEXT("ChainMin"), ChainMinNode);
    MaterialGenerator::AddCustomInput(CustomNode, TEXT("ChainMax"), ChainMaxNode);
    MaterialGenerator::AddCustomInput(CustomNode, TEXT("GlyphGrid"), GlyphGridAppendNode);
    MaterialGenerator::AddCustomInput(CustomNode, TEXT("GlyphCount"), GlyphCountNode);
    MaterialGenerator::AddCustomInput(CustomNode, TEXT("GlyphAtlas"), GlyphAtlasNode);

    UMaterialExpressionComponentMask* ColorMaskNode = MaterialGenerator::CreateExpression<UMaterialExpressionComponentMask>(MasterMaterial, -140, 220);
    UMaterialExpressionComponentMask* AlphaMaskNode = MaterialGenerator::CreateExpression<UMaterialExpressionComponentMask>(MasterMaterial, -140, 520);
    UMaterialExpressionScalarParameter* EmissiveStrengthNode = MaterialGenerator::CreateScalarParameter(MasterMaterial, TEXT("EmissiveStrength"), 1.6f, -140, 360);
    UMaterialExpressionMultiply* EmissiveMultiplyNode = MaterialGenerator::CreateExpression<UMaterialExpressionMultiply>(MasterMaterial, 120, 280);

    if (ColorMaskNode)
    {
        ColorMaskNode->Input.Expression = CustomNode;
        ColorMaskNode->R = true;
        ColorMaskNode->G = true;
        ColorMaskNode->B = true;
        ColorMaskNode->A = false;
    }

    if (AlphaMaskNode)
    {
        AlphaMaskNode->Input.Expression = CustomNode;
        AlphaMaskNode->R = false;
        AlphaMaskNode->G = false;
        AlphaMaskNode->B = false;
        AlphaMaskNode->A = true;
    }

    if (EmissiveMultiplyNode)
    {
        EmissiveMultiplyNode->A.Expression = ColorMaskNode;
        EmissiveMultiplyNode->B.Expression = EmissiveStrengthNode;
    }

    if (!EmissiveMultiplyNode || !AlphaMaskNode)
    {
        Result.Message = TEXT("Failed to create output wiring nodes.");
        return Result;
    }

    UMaterialEditingLibrary::ConnectMaterialProperty(EmissiveMultiplyNode, TEXT(""), MP_EmissiveColor);
    UMaterialEditingLibrary::ConnectMaterialProperty(AlphaMaskNode, TEXT(""), MP_Opacity);

    UMaterialEditingLibrary::LayoutMaterialExpressions(MasterMaterial);
    UMaterialEditingLibrary::RecompileMaterial(MasterMaterial);

    MasterMaterial->PostEditChange();
    MasterMaterial->MarkPackageDirty();

    if (bCreatedMaster)
    {
        FAssetRegistryModule::AssetCreated(MasterMaterial);
    }

    Result.MasterMaterial = MasterMaterial;

    if (Config.bCreateMaterialInstance)
    {
        bool bCreatedInstance = false;
        UMaterialInstanceConstant* Instance = MaterialGenerator::LoadOrCreateAsset<UMaterialInstanceConstant, UMaterialInstanceConstantFactoryNew>(PackagePath, InstanceName, bCreatedInstance);
        if (!Instance)
        {
            Result.Message = TEXT("Master material generated, but failed to create or load material instance.");
            return Result;
        }

        Instance->Modify();
#if ENGINE_MAJOR_VERSION >= 5
        Instance->SetParentEditorOnly(MasterMaterial);
#else
        Instance->Parent = MasterMaterial;
#endif

        if (GlyphTexture)
        {
            UMaterialEditingLibrary::SetMaterialInstanceTextureParameterValue(Instance, FName(TEXT("GlyphAtlas")), GlyphTexture);
        }

        UMaterialEditingLibrary::SetMaterialInstanceScalarParameterValue(Instance, FName(TEXT("Tiling")), 1.0f);
        UMaterialEditingLibrary::SetMaterialInstanceScalarParameterValue(Instance, FName(TEXT("PackX")), 1.0f);
        UMaterialEditingLibrary::SetMaterialInstanceScalarParameterValue(Instance, FName(TEXT("PackY")), 1.0f);
        UMaterialEditingLibrary::SetMaterialInstanceScalarParameterValue(Instance, FName(TEXT("Direction")), 0.0f);
        UMaterialEditingLibrary::SetMaterialInstanceScalarParameterValue(Instance, FName(TEXT("AnimSpeed")), 1.0f);
        UMaterialEditingLibrary::SetMaterialInstanceScalarParameterValue(Instance, FName(TEXT("Softness")), 0.35f);
        UMaterialEditingLibrary::SetMaterialInstanceScalarParameterValue(Instance, FName(TEXT("LifeMin")), 3.0f);
        UMaterialEditingLibrary::SetMaterialInstanceScalarParameterValue(Instance, FName(TEXT("LifeMax")), 14.0f);
        UMaterialEditingLibrary::SetMaterialInstanceScalarParameterValue(Instance, FName(TEXT("ChainMin")), 0.8f);
        UMaterialEditingLibrary::SetMaterialInstanceScalarParameterValue(Instance, FName(TEXT("ChainMax")), 2.2f);
        UMaterialEditingLibrary::SetMaterialInstanceScalarParameterValue(Instance, FName(TEXT("GlyphGridX")), 16.0f);
        UMaterialEditingLibrary::SetMaterialInstanceScalarParameterValue(Instance, FName(TEXT("GlyphGridY")), 6.0f);
        UMaterialEditingLibrary::SetMaterialInstanceScalarParameterValue(Instance, FName(TEXT("GlyphCount")), 96.0f);
        UMaterialEditingLibrary::SetMaterialInstanceScalarParameterValue(Instance, FName(TEXT("EmissiveStrength")), 1.6f);

        UMaterialEditingLibrary::SetMaterialInstanceVectorParameterValue(Instance, FName(TEXT("Bg")), FLinearColor(0.01f, 0.03f, 0.01f, 1.0f));

        Instance->PostEditChange();
        Instance->MarkPackageDirty();

        if (bCreatedInstance)
        {
            FAssetRegistryModule::AssetCreated(Instance);
        }

        Result.MaterialInstance = Instance;
    }

    const FString MasterPath = FString::Printf(TEXT("%s/%s"), *PackagePath, *MasterName);
    if (Result.MaterialInstance)
    {
        const FString InstancePath = FString::Printf(TEXT("%s/%s"), *PackagePath, *InstanceName);
        Result.Message = FString::Printf(TEXT("Created material: %s and instance: %s"), *MasterPath, *InstancePath);
    }
    else
    {
        Result.Message = FString::Printf(TEXT("Created material: %s"), *MasterPath);
    }

    Result.bSuccess = true;
    return Result;
}








