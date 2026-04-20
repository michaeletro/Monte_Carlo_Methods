#include <vector>

namespace ExactSDE {
    // Known solution, dS = aSdt + bSdz, S(0) = S0
    double a = 0.1; // drift coefficient
    double b = 0.2; // diffusion coefficient
    double S0 = 100.0; // initial condition
    
    double drift(double t, double S) {
        return a * S;
    }

    double diffusion(double t, double S) {
        return b * S;
    }
}

using namespace ExactSDE;

class OneFactorSDE {
    private:
        double ic; // initial condition
        Range<double> timeRange; // time range for the SDE
    public:
        OneFactorSDE(double ic_, Range<double> timeRange_) : ic(ic_), timeRange(timeRange_) {}
        const double& InitialCondition() const {return ic;}
        const Range<double>& Interval() const {return timeRange;}
        double getExpiry() const {return timeRange.end;}

        // Functional Extension 
        virtual void Accept(class SDEVisitor& visitor) = 0;
};

class SDETypeD : public OneFactorSDE {
    private:
        // Additional parameters specific to SDETypeD can be added here
        double (*drift) (double t, double x); // Drift function
        double (*diffusion) (double t, double X); // Diffusion function

    public:
        SDETypeD() : OneFactorSDE(0.0, Range<double>{0.0, 1.0}) {}
        SDETypeD(double ic_, Range<double> timeRange_, double (*drift_)(double, double), double (*diffusion_)(double, double)) 
            : OneFactorSDE(ic_, timeRange_), drift(drift_), diffusion(diffusion_) {}
        double calculateDrift(double t, double X) const { return drift(t,X); }
        double calculateDiffusion(double t, double X) const { return diffusion(t,X); }
        
        void Accept(SDEVisitor& visitor) override;
};