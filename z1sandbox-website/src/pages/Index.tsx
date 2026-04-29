import HeroSection from "@/components/HeroSection";
import TrustSection from "@/components/TrustSection";
import PillarsSection from "@/components/PillarsSection";
import FeaturesSection from "@/components/FeaturesSection";
import SecurityPipeline from "@/components/SecurityPipeline";
// import ArchitectureSection from "@/components/ArchitectureSection";
import WhySection from "@/components/WhySection";
import LanguagesSection from "@/components/LanguagesSection";

const Index = () => {
  return (
    <>
      <HeroSection />
      <TrustSection />
      <div id="pillars"><PillarsSection /></div>
      <LanguagesSection />
      <div id="features"><FeaturesSection /></div>
      <div id="security"><SecurityPipeline /></div>
      {/* <ArchitectureSection /> */}
      <div id="why"><WhySection /></div>
    </>
  );
};

export default Index;
