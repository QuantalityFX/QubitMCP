#include "MaterialGeneratorModule.h"

#include "SMaterialGeneratorPanel.h"
#include "Framework/Docking/TabManager.h"
#include "Styling/AppStyle.h"
#include "ToolMenus.h"
#include "Widgets/Docking/SDockTab.h"

#define LOCTEXT_NAMESPACE "FMaterialGeneratorModule"

static const FName MaterialGeneratorTabName(TEXT("MaterialGeneratorTab"));

void FMaterialGeneratorModule::StartupModule()
{
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
}

void FMaterialGeneratorModule::RegisterMenus()
{
    FToolMenuOwnerScoped OwnerScoped(this);

    if (UToolMenu* Menu = UToolMenus::Get()->ExtendMenu("LevelEditor.MainMenu.Window"))
    {
        FToolMenuSection& Section = Menu->FindOrAddSection("WindowLayout");
        Section.AddMenuEntry(
            "MaterialGenerator_OpenWindow",
            LOCTEXT("MaterialGenerator_Menu", "Material Generator"),
            LOCTEXT("MaterialGenerator_MenuTooltip", "Open the Material Generator window."),
            FSlateIcon(),
            FUIAction(FExecuteAction::CreateRaw(this, &FMaterialGeneratorModule::OpenPluginWindow)));
    }

    if (UToolMenu* ToolbarMenu = UToolMenus::Get()->ExtendMenu("LevelEditor.LevelEditorToolBar"))
    {
        FToolMenuSection& Section = ToolbarMenu->FindOrAddSection("Settings");
        FToolMenuEntry Entry = FToolMenuEntry::InitToolBarButton(
            "MaterialGenerator_ToolbarButton",
            FUIAction(FExecuteAction::CreateRaw(this, &FMaterialGeneratorModule::OpenPluginWindow)),
            LOCTEXT("MaterialGenerator_ToolbarLabel", "Material Generator"),
            LOCTEXT("MaterialGenerator_ToolbarTooltip", "Open the Material Generator window."),
            FSlateIcon(FAppStyle::GetAppStyleSetName(), "LevelEditor.GameSettings"));
        Section.AddEntry(Entry);
    }
}

void FMaterialGeneratorModule::OpenPluginWindow()
{
    FGlobalTabmanager::Get()->TryInvokeTab(MaterialGeneratorTabName);
}

#undef LOCTEXT_NAMESPACE

IMPLEMENT_MODULE(FMaterialGeneratorModule, MaterialGenerator)

