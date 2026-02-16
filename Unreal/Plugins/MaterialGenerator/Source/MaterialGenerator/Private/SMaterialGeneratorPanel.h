#pragma once

#include "CoreMinimal.h"
#include "Widgets/SCompoundWidget.h"
#include "Input/Reply.h"
#include "Widgets/Input/SCheckBox.h"
#include "Widgets/Input/SEditableTextBox.h"




class SMaterialGeneratorPanel : public SCompoundWidget
{
public:
    SLATE_BEGIN_ARGS(SMaterialGeneratorPanel) {}
    SLATE_END_ARGS()

    void Construct(const FArguments& InArgs);

private:
    FReply OnGenerateClicked();
    void LogMessage(const FText& Message, bool bIsError = false) const;

    FString GetPathText() const;
    FString GetMasterNameText() const;
    FString GetInstanceNameText() const;
    FString GetGlyphTexturePathText() const;

private:
    TSharedPtr<SEditableTextBox> PackagePathTextBox;
    TSharedPtr<SEditableTextBox> MasterNameTextBox;
    TSharedPtr<SEditableTextBox> InstanceNameTextBox;
    TSharedPtr<SEditableTextBox> GlyphTexturePathTextBox;
    TSharedPtr<SCheckBox> CreateInstanceCheckBox;
};


