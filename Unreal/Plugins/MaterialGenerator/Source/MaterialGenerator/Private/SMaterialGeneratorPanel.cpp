#include "SMaterialGeneratorPanel.h"

#include "MaterialGeneratorBuilder.h"

#include "AssetRegistry/AssetData.h"
#include "DesktopPlatformModule.h"
#include "Engine/Texture.h"
#include "Framework/Application/SlateApplication.h"
#include "Framework/Notifications/NotificationManager.h"
#include "GenericPlatform/GenericWindow.h"
#include "IDesktopPlatform.h"
#include "Misc/Paths.h"
#include "PropertyCustomizationHelpers.h"
#include "Widgets/Input/SButton.h"
#include "Widgets/Input/SCheckBox.h"
#include "Widgets/Input/SEditableTextBox.h"
#include "Widgets/Layout/SBox.h"
#include "Widgets/SBoxPanel.h"
#include "Widgets/SWindow.h"
#include "Widgets/Layout/SUniformGridPanel.h"
#include "Widgets/Notifications/SNotificationList.h"
#include "Widgets/Text/STextBlock.h"

#define LOCTEXT_NAMESPACE "SMaterialGeneratorPanel"

void SMaterialGeneratorPanel::Construct(const FArguments& InArgs)
{
    ChildSlot
    [
        SNew(SBox)
        .WidthOverride(360.0f)
        [
            SNew(SVerticalBox)

            + SVerticalBox::Slot()
            .AutoHeight()
            .Padding(8.0f)
            [
                SNew(STextBlock)
                .Text(LOCTEXT("Title", "Matrix Rain Material Generator"))
            ]

            + SVerticalBox::Slot()
            .AutoHeight()
            .Padding(8.0f, 4.0f)
            [
                SNew(STextBlock)
                .AutoWrapText(true)
                .Text(LOCTEXT("Description", "Generates a master Matrix Rain material with all custom-node inputs and optional default material instance."))
            ]

            + SVerticalBox::Slot()
            .AutoHeight()
            .Padding(8.0f, 8.0f, 8.0f, 0.0f)
            [
                SNew(STextBlock).Text(LOCTEXT("PackagePathLabel", "Package Path"))
            ]
            + SVerticalBox::Slot()
            .AutoHeight()
            .Padding(8.0f, 4.0f)
            [
                SNew(SHorizontalBox)
                + SHorizontalBox::Slot()
                .FillWidth(1.0f)
                [
                    SAssignNew(PackagePathTextBox, SEditableTextBox)
                    .HintText(LOCTEXT("PackagePathHint", "Examples: Qubit/Materials, /Game/Qubit/Materials, or .../Content/Qubit/Materials"))
                    .Text(FText::FromString(TEXT("Materials")))
                ]
                + SHorizontalBox::Slot()
                .AutoWidth()
                .Padding(6.0f, 0.0f, 0.0f, 0.0f)
                [
                    SNew(SButton)
                    .ToolTipText(LOCTEXT("BrowsePackagePathTooltip", "Pick a folder under your Unreal project's Content directory."))
                    .OnClicked(this, &SMaterialGeneratorPanel::OnBrowsePackagePathClicked)
                    [
                        SNew(STextBlock)
                        .Text(LOCTEXT("BrowsePackagePathButton", "Browse..."))
                        .Justification(ETextJustify::Center)
                    ]
                ]
            ]

            + SVerticalBox::Slot()
            .AutoHeight()
            .Padding(8.0f, 8.0f, 8.0f, 0.0f)
            [
                SNew(STextBlock).Text(LOCTEXT("MasterNameLabel", "Master Material Name"))
            ]
            + SVerticalBox::Slot()
            .AutoHeight()
            .Padding(8.0f, 4.0f)
            [
                SAssignNew(MasterNameTextBox, SEditableTextBox)
                .Text(FText::FromString(TEXT("M_Master_MatrixRain")))
            ]

            + SVerticalBox::Slot()
            .AutoHeight()
            .Padding(8.0f, 8.0f, 8.0f, 0.0f)
            [
                SNew(STextBlock).Text(LOCTEXT("GlyphPathLabel", "Glyph Texture (Optional)"))
            ]
            + SVerticalBox::Slot()
            .AutoHeight()
            .Padding(8.0f, 4.0f)
            [
                SNew(SObjectPropertyEntryBox)
                .AllowedClass(UTexture::StaticClass())
                .DisplayThumbnail(false)
                .AllowClear(true)
                .ObjectPath(this, &SMaterialGeneratorPanel::GetGlyphTexturePathText)
                .OnObjectChanged(this, &SMaterialGeneratorPanel::OnGlyphTextureChanged)
            ]

            + SVerticalBox::Slot()
            .AutoHeight()
            .Padding(8.0f, 8.0f, 8.0f, 0.0f)
            [
                SNew(STextBlock).Text(LOCTEXT("InstanceNameLabel", "Material Instance Name"))
            ]
            + SVerticalBox::Slot()
            .AutoHeight()
            .Padding(8.0f, 4.0f)
            [
                SAssignNew(InstanceNameTextBox, SEditableTextBox)
                .Text(FText::FromString(TEXT("MI_MatrixRain_Default")))
            ]

            + SVerticalBox::Slot()
            .AutoHeight()
            .Padding(8.0f, 8.0f)
            [
                SAssignNew(CreateInstanceCheckBox, SCheckBox)
                .IsChecked(ECheckBoxState::Checked)
                [
                    SNew(STextBlock)
                    .Text(LOCTEXT("CreateInstanceLabel", "Also create default material instance"))
                ]
            ]

            + SVerticalBox::Slot()
            .AutoHeight()
            .Padding(8.0f)
            [
                SNew(SHorizontalBox)
                + SHorizontalBox::Slot()
                .HAlign(HAlign_Center)
                .FillWidth(1.0f)
                [
                    SNew(SBox)
                    .MinDesiredWidth(240.0f)
                    .MinDesiredHeight(44.0f)
                    [
                        SNew(SButton)
                        .ButtonColorAndOpacity(FLinearColor(0.20f, 0.82f, 0.28f, 1.0f))
                        .HAlign(HAlign_Center)
                        .VAlign(VAlign_Center)
                        .ContentPadding(FMargin(14.0f, 8.0f))
                        .OnClicked(this, &SMaterialGeneratorPanel::OnGenerateClicked)
                        [
                            SNew(STextBlock)
                            .Text(LOCTEXT("GenerateButton", "Generate Material"))
                            .Justification(ETextJustify::Center)
                        ]
                    ]
                ]
            ]
        ]
    ];
}

FReply SMaterialGeneratorPanel::OnGenerateClicked()
{
    FMaterialGeneratorConfig Config;
    Config.PackagePath = GetPathText();
    Config.MasterMaterialName = GetMasterNameText();
    Config.MaterialInstanceName = GetInstanceNameText();
    Config.GlyphTextureObjectPath = GetGlyphTexturePathText();
    Config.bCreateMaterialInstance = CreateInstanceCheckBox.IsValid() && (CreateInstanceCheckBox->GetCheckedState() == ECheckBoxState::Checked);

    const FMaterialGeneratorResult Result = FMaterialGeneratorBuilder::CreateMatrixRainMaterial(Config);

    if (Result.bSuccess)
    {
        LogMessage(FText::FromString(Result.Message), false);
    }
    else
    {
        LogMessage(FText::FromString(Result.Message), true);
    }

    return FReply::Handled();
}

FReply SMaterialGeneratorPanel::OnBrowsePackagePathClicked()
{
    IDesktopPlatform* DesktopPlatform = FDesktopPlatformModule::Get();
    if (!DesktopPlatform)
    {
        LogMessage(LOCTEXT("DesktopPlatformUnavailable", "Desktop platform dialog is unavailable on this system."), true);
        return FReply::Handled();
    }

    void* ParentWindowHandle = nullptr;
    if (TSharedPtr<SWindow> ParentWindow = FSlateApplication::Get().FindWidgetWindow(AsShared()))
    {
        const TSharedPtr<FGenericWindow> NativeWindow = ParentWindow->GetNativeWindow();
        if (NativeWindow.IsValid())
        {
            ParentWindowHandle = NativeWindow->GetOSWindowHandle();
        }
    }

    FString SelectedFolder;
    const FString DefaultDir = FPaths::ProjectContentDir();
    const bool bSelected = DesktopPlatform->OpenDirectoryDialog(
        ParentWindowHandle,
        TEXT("Select Material Output Folder (inside Content)"),
        DefaultDir,
        SelectedFolder);

    if (!bSelected || SelectedFolder.IsEmpty())
    {
        return FReply::Handled();
    }

    if (PackagePathTextBox.IsValid())
    {
        PackagePathTextBox->SetText(FText::FromString(SelectedFolder));
    }

    return FReply::Handled();
}

void SMaterialGeneratorPanel::LogMessage(const FText& Message, bool bIsError) const
{
    FNotificationInfo Info(Message);
    Info.ExpireDuration = bIsError ? 7.0f : 4.0f;
    Info.bUseSuccessFailIcons = true;

    TSharedPtr<SNotificationItem> Item = FSlateNotificationManager::Get().AddNotification(Info);
    if (Item.IsValid())
    {
        Item->SetCompletionState(bIsError ? SNotificationItem::CS_Fail : SNotificationItem::CS_Success);
    }
}

FString SMaterialGeneratorPanel::GetPathText() const
{
    return PackagePathTextBox.IsValid() ? PackagePathTextBox->GetText().ToString().TrimStartAndEnd() : TEXT("Materials");
}

FString SMaterialGeneratorPanel::GetMasterNameText() const
{
    return MasterNameTextBox.IsValid() ? MasterNameTextBox->GetText().ToString().TrimStartAndEnd() : TEXT("M_Master_MatrixRain");
}

FString SMaterialGeneratorPanel::GetInstanceNameText() const
{
    return InstanceNameTextBox.IsValid() ? InstanceNameTextBox->GetText().ToString().TrimStartAndEnd() : TEXT("MI_MatrixRain_Default");
}

FString SMaterialGeneratorPanel::GetGlyphTexturePathText() const
{
    return GlyphTexture.IsValid() ? GlyphTexture->GetPathName() : TEXT("");
}

void SMaterialGeneratorPanel::OnGlyphTextureChanged(const FAssetData& InAssetData)
{
    if (!InAssetData.IsValid())
    {
        GlyphTexture.Reset();
        return;
    }

    GlyphTexture = Cast<UTexture>(InAssetData.GetAsset());
}

#undef LOCTEXT_NAMESPACE


