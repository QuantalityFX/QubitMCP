using UnrealBuildTool;

public class MaterialGenerator : ModuleRules
{
    public MaterialGenerator(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = ModuleRules.PCHUsageMode.UseExplicitOrSharedPCHs;

        PublicDependencyModuleNames.AddRange(new[]
        {
            "Core",
            "CoreUObject",
            "Engine",
            "Slate",
            "SlateCore"
        });

        PrivateDependencyModuleNames.AddRange(new[]
        {
            "ApplicationCore",
            "AssetRegistry",
            "AssetTools",
            "ContentBrowser",
            "EditorStyle",
            "InputCore",
            "LevelEditor",
            "MaterialEditor",
            "Projects",
            "ToolMenus",
            "UnrealEd"
        });
    }
}
