#pragma once

#include "CoreMinimal.h"
#include "Modules/ModuleManager.h"

class FSlateStyleSet;

class FMaterialGeneratorModule : public IModuleInterface
{
public:
    virtual void StartupModule() override;
    virtual void ShutdownModule() override;

private:
    void RegisterStyle();
    void UnregisterStyle();
    void RegisterMenus();
    void OpenPluginWindow();

private:
    TSharedPtr<FSlateStyleSet> StyleSet;
};
