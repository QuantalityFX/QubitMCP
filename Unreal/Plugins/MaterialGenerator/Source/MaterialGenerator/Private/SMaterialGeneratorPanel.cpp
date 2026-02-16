#include "SMaterialGeneratorPanel.h"

#include "MaterialGeneratorBuilder.h"

#include "Framework/Notifications/NotificationManager.h"
#include "Widgets/Input/SButton.h"
#include "Widgets/Input/SCheckBox.h"
#include "Widgets/Input/SEditableTextBox.h"
#include "Widgets/Layout/SBox.h"
#include "Widgets/SBoxPanel.h"
#include "Widgets/Layout/SUniformGridPanel.h"
#include "Widgets/Notifications/SNotificationList.h"
#include "Widgets/Text/STextBlock.h"

#define LOCTEXT_NAMESPACE "SMaterialGeneratorPanel"

void SMaterialGeneratorPanel::Construct(const FArguments& InArgs)
{
    ChildSlot
    [
        SNew(SBox)
        .WidthOverride(520.0f)
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
                .Text(LOCTEXT("Description", "Generates a master Matrix Rain material with all custom-node inputs and optional default material instance."))
            ]

            + SVerticalBox::Slot()
            .AutoHeight()
            .Padding(8.0f, 8.0f, 8.0f, 0.0f)
            [
                SNew(STextBlock).Text(LOCTEXT("PackagePathLabel", "Package Path (Content-relative, /Game optional)"))
            ]
            + SVerticalBox::Slot()
            .AutoHeight()
            .Padding(8.0f, 4.0f)
            [
                SAssignNew(PackagePathTextBox, SEditableTextBox)
                .HintText(LOCTEXT("PackagePathHint", "Examples: Qubit/Materials, /Game/Qubit/Materials, or .../Content/Qubit/Materials"))
                .Text(FText::FromString(TEXT("Materials")))
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
                SNew(STextBlock).Text(LOCTEXT("GlyphPathLabel", "Glyph Texture Object Path (Optional)"))
            ]
            + SVerticalBox::Slot()
            .AutoHeight()
            .Padding(8.0f, 4.0f)
            [
                SAssignNew(GlyphTexturePathTextBox, SEditableTextBox)
                .HintText(LOCTEXT("GlyphPathHint", "Example: /Game/Textures/T_MatrixGlyphAtlas.T_MatrixGlyphAtlas"))
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
                SNew(SUniformGridPanel)
                .SlotPadding(4.0f)
                + SUniformGridPanel::Slot(0, 0)
                [
                    SNew(SButton)
                    .Text(LOCTEXT("GenerateButton", "Generate Material"))
                    .OnClicked(this, &SMaterialGeneratorPanel::OnGenerateClicked)
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
    return GlyphTexturePathTextBox.IsValid() ? GlyphTexturePathTextBox->GetText().ToString().TrimStartAndEnd() : TEXT("");
}

#undef LOCTEXT_NAMESPACE


