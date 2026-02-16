#include "MaterialGeneratorModule.h"

#include "SMaterialGeneratorPanel.h"

#include "Brushes/SlateImageBrush.h"
#include "Framework/Docking/TabManager.h"
#include "Interfaces/IPluginManager.h"
#include "Misc/Paths.h"
#include "Styling/AppStyle.h"
#include "Styling/SlateStyle.h"
#include "Styling/SlateStyleRegistry.h"
#include "ToolMenus.h"
#include "Widgets/Docking/SDockTab.h"

#define LOCTEXT_NAMESPACE "FMaterialGeneratorModule"

static const FName MaterialGeneratorTabName(TEXT("MaterialGeneratorTab"));
static const FName MaterialGeneratorStyleName(TEXT("MaterialGeneratorStyle"));
static const FName MaterialGeneratorIconName(TEXT("MaterialGenerator.OpenWindow"));
static const FName MaterialGeneratorIconSmallName(TEXT("MaterialGenerator.OpenWindow.Small"));

void FMaterialGeneratorModule::StartupModule()
{
    RegisterStyle();

    FGlobalTabmanager::Get()->RegisterNomadTabSpawner(
        MaterialGeneratorTabName,
        FOnSpawnTab::CreateLambda([](const FSpawnTabArgs&)
        {
            return SNew(SDockTab)
                .TabRole(ETabRole::NomadTab)
                [
                    SNew(SMaterialGeneratorPanel)
                ];
        }))
        .SetDisplayName(LOCTEXT("MaterialGeneratorTabTitle", "Material Generator"))
        .SetTooltipText(LOCTEXT("MaterialGeneratorTooltip", "Generate Matrix Rain materials."));

    UToolMenus::RegisterStartupCallback(
        FSimpleMulticastDelegate::FDelegate::CreateRaw(this, &FMaterialGeneratorModule::RegisterMenus));
}

void FMaterialGeneratorModule::ShutdownModule()
{
    if (UToolMenus::IsToolMenuUIEnabled())
    {
        UToolMenus::UnRegisterStartupCallback(this);
        UToolMenus::UnregisterOwner(this);
    }

    FGlobalTabmanager::Get()->UnregisterNomadTabSpawner(MaterialGeneratorTabName);
    UnregisterStyle();
}

void FMaterialGeneratorModule::RegisterStyle()
{
    if (StyleSet.IsValid())
    {
        return;
    }

    const TSharedPtr<IPlugin> Plugin = IPluginManager::Get().FindPlugin(TEXT("MaterialGenerator"));
    if (!Plugin.IsValid())
    {
        return;
    }

    StyleSet = MakeShared<FSlateStyleSet>(MaterialGeneratorStyleName);
    StyleSet->SetContentRoot(FPaths::Combine(Plugin->GetBaseDir(), TEXT("Resources")));
    StyleSet->Set(MaterialGeneratorIconName, new FSlateImageBrush(StyleSet->RootToContentDir(TEXT("MaterialGenerator_Icon_S"), TEXT(".png")), FVector2D(20.0f, 20.0f)));
    StyleSet->Set(MaterialGeneratorIconSmallName, new FSlateImageBrush(StyleSet->RootToContentDir(TEXT("MaterialGenerator_Icon_S"), TEXT(".png")), FVector2D(16.0f, 16.0f)));

    FSlateStyleRegistry::RegisterSlateStyle(*StyleSet.Get());
}

void FMaterialGeneratorModule::UnregisterStyle()
{
    if (!StyleSet.IsValid())
    {
        return;
    }

    FSlateStyleRegistry::UnRegisterSlateStyle(*StyleSet.Get());
    StyleSet.Reset();
}

void FMaterialGeneratorModule::RegisterMenus()
{
    FToolMenuOwnerScoped OwnerScoped(this);

    const FSlateIcon PluginIcon = StyleSet.IsValid()
        ? FSlateIcon(StyleSet->GetStyleSetName(), MaterialGeneratorIconName, MaterialGeneratorIconSmallName)
        : FSlateIcon(FAppStyle::GetAppStyleSetName(), "LevelEditor.GameSettings");

    if (UToolMenu* Menu = UToolMenus::Get()->ExtendMenu("LevelEditor.MainMenu.Window"))
    {
        FToolMenuSection& Section = Menu->FindOrAddSection("WindowLayout");
        Section.AddMenuEntry(
            "MaterialGenerator_OpenWindow",
            LOCTEXT("MaterialGenerator_Menu", "Material Generator"),
            LOCTEXT("MaterialGenerator_MenuTooltip", "Open the Material Generator window."),
            PluginIcon,
            FUIAction(FExecuteAction::CreateRaw(this, &FMaterialGeneratorModule::OpenPluginWindow)));
    }

    bool bAddedNearPlay = false;
    if (UToolMenu* PlayToolbarMenu = UToolMenus::Get()->ExtendMenu("LevelEditor.LevelEditorToolBar.PlayToolBar"))
    {
        FToolMenuSection& Section = PlayToolbarMenu->FindOrAddSection("Play");

        FToolMenuEntry Entry = FToolMenuEntry::InitToolBarButton(
            "MaterialGenerator_ToolbarButton_Play",
            FUIAction(FExecuteAction::CreateRaw(this, &FMaterialGeneratorModule::OpenPluginWindow)),
            LOCTEXT("MaterialGenerator_ToolbarLabel", "Material Generator"),
            LOCTEXT("MaterialGenerator_ToolbarTooltip", "Open the Material Generator window."),
            PluginIcon);
        Section.AddEntry(Entry);
        bAddedNearPlay = true;
    }

    if (!bAddedNearPlay)
    {
        if (UToolMenu* ToolbarMenu = UToolMenus::Get()->ExtendMenu("LevelEditor.LevelEditorToolBar"))
        {
            FToolMenuSection& Section = ToolbarMenu->FindOrAddSection("Settings");
            FToolMenuEntry Entry = FToolMenuEntry::InitToolBarButton(
                "MaterialGenerator_ToolbarButton",
                FUIAction(FExecuteAction::CreateRaw(this, &FMaterialGeneratorModule::OpenPluginWindow)),
                LOCTEXT("MaterialGenerator_ToolbarLabel_Fallback", "Material Generator"),
                LOCTEXT("MaterialGenerator_ToolbarTooltip_Fallback", "Open the Material Generator window."),
                PluginIcon);
            Section.AddEntry(Entry);
        }
    }
}

void FMaterialGeneratorModule::OpenPluginWindow()
{
    FGlobalTabmanager::Get()->TryInvokeTab(MaterialGeneratorTabName);
}

#undef LOCTEXT_NAMESPACE

IMPLEMENT_MODULE(FMaterialGeneratorModule, MaterialGenerator)
