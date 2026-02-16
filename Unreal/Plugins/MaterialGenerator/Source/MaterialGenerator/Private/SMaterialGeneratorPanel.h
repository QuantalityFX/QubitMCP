#pragma once

#include "CoreMinimal.h"
#include "Input/Reply.h"
#include "Widgets/Input/SCheckBox.h"
#include "Widgets/Input/SEditableTextBox.h"
#include "Widgets/SCompoundWidget.h"

class UTexture;
struct FAssetData;

class SMaterialGeneratorPanel : public SCompoundWidget
{
public:
    SLATE_BEGIN_ARGS(SMaterialGeneratorPanel) {}
    SLATE_END_ARGS()

    void Construct(const FArguments& InArgs);

private:
    FReply OnGenerateClicked();
    FReply OnBrowsePackagePathClicked();
    void LogMessage(const FText& Message, bool bIsError = false) const;

    FString GetPathText() const;
    FString GetMasterNameText() const;
    FString GetInstanceNameText() const;
    FString GetGlyphTexturePathText() const;
    void OnGlyphTextureChanged(const FAssetData& InAssetData);

private:
    TSharedPtr<SEditableTextBox> PackagePathTextBox;
    TSharedPtr<SEditableTextBox> MasterNameTextBox;
    TSharedPtr<SEditableTextBox> InstanceNameTextBox;
    TSharedPtr<SCheckBox> CreateInstanceCheckBox;
    TWeakObjectPtr<UTexture> GlyphTexture;
};
