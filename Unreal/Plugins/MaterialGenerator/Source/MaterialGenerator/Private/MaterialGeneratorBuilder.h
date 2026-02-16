#pragma once

#include "CoreMinimal.h"

class UMaterial;

struct FMaterialGeneratorConfig
{
    FString PackagePath = TEXT("/Game/Materials");
    FString MasterMaterialName = TEXT("M_Master_MatrixRain");
    bool bCreateMaterialInstance = true;
    FString MaterialInstanceName = TEXT("MI_MatrixRain_Default");
    FString GlyphTextureObjectPath;
};

struct FMaterialGeneratorResult
{
    bool bSuccess = false;
    FString Message;
    UMaterial* MasterMaterial = nullptr;
    class UMaterialInstanceConstant* MaterialInstance = nullptr;
};

class FMaterialGeneratorBuilder
{
public:
    static FMaterialGeneratorResult CreateMatrixRainMaterial(const FMaterialGeneratorConfig& Config);
};
