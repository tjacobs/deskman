#pragma once

#include <SDL2/SDL.h>
#include <vector>
#include <cmath>
#include "screen.h"

using namespace std;

// 3D Vector class for transformations
struct Vec3 {
    float x, y, z;
    Vec3(float x = 0, float y = 0, float z = 0) : x(x), y(y), z(z) {}
    
    Vec3 operator+(const Vec3& other) const {
        return Vec3(x + other.x, y + other.y, z + other.z);
    }
    
    Vec3 operator*(float scalar) const {
        return Vec3(x * scalar, y * scalar, z * scalar);
    }
    
    // Apply rotation matrix to vector
    Vec3 rotateX(float angle) const {
        float rad = angle * M_PI / 180.0f;
        float cosA = cos(rad);
        float sinA = sin(rad);
        return Vec3(
            x,
            y * cosA - z * sinA,
            y * sinA + z * cosA
        );
    }
    
    Vec3 rotateY(float angle) const {
        float rad = angle * M_PI / 180.0f;
        float cosA = cos(rad);
        float sinA = sin(rad);
        return Vec3(
            x * cosA + z * sinA,
            y,
            -x * sinA + z * cosA
        );
    }
    
    Vec3 rotateZ(float angle) const {
        float rad = angle * M_PI / 180.0f;
        float cosA = cos(rad);
        float sinA = sin(rad);
        return Vec3(
            x * cosA - y * sinA,
            x * sinA + y * cosA,
            z
        );
    }
};

// Projection utilities
namespace Projection {
    // Project 3D point to 2D screen coordinates with perspective
    SDL_Point project(const Vec3& point, const Vec3& faceRotation);
}

// Base class for all vector shapes
class VectorShape {
public:
    Vec3 localPosition;  // Position relative to the face plane
    Vec3 rotation;  // Euler angles in degrees
    Vec3 scale;
    SDL_Color fillColor;
    SDL_Color strokeColor;
    float strokeWidth;
    bool visible;
    
    VectorShape() : localPosition(0, 0, 0), rotation(0, 0, 0), scale(1, 1, 1),
                   fillColor({0, 0, 0, 255}), strokeColor({0, 0, 0, 255}), strokeWidth(1.0f),
                   visible(true) {}
    virtual ~VectorShape() {}
    
    virtual void render(SDL_Renderer* renderer, const Vec3& facePosition, const Vec3& faceRotation) = 0;
};

// Helper function to draw a larger dot
void drawLargeDot(SDL_Renderer* renderer, int x, int y);

// Circle shape
class Circle : public VectorShape {
public:
    float radius;
    
    Circle(float radius = 50, SDL_Color fillColor = {0, 0, 0, 255}, 
           SDL_Color strokeColor = {0, 0, 0, 255}, float strokeWidth = 1.0f) 
        : radius(radius) {
        this->fillColor = fillColor;
        this->strokeColor = strokeColor;
        this->strokeWidth = strokeWidth;
    }
    
    void render(SDL_Renderer* renderer, const Vec3& facePosition, const Vec3& faceRotation) override {
        SDL_SetRenderDrawColor(renderer, fillColor.r, fillColor.g, fillColor.b, fillColor.a);
        
        // Calculate effective radius based on perspective
        float effectiveRadius = radius * (1.0f / (1.0f + (localPosition.z + facePosition.z) * 0.001f));
        
        // Draw filled circle by projecting each point in 3D space
        // Use step size of 2 to reduce number of points
        for (int y = -effectiveRadius; y <= effectiveRadius; y += 2) {
            for (int x = -effectiveRadius; x <= effectiveRadius; x += 2) {
                if (x*x + y*y <= effectiveRadius*effectiveRadius) {
                    // Create point in local space
                    Vec3 point(x, y, 0);
                    // Add shape position and face position
                    point = point + localPosition + facePosition;
                    // Project to screen
                    SDL_Point screenPoint = Projection::project(point, faceRotation);
                    drawLargeDot(renderer, screenPoint.x, screenPoint.y);
                }
            }
        }
    }
};

// Ellipse shape
class Ellipse : public VectorShape {
public:
    float radiusX, radiusY;
    float cutoutY;  // Y position of cutout center
    float cutoutHeight;  // Height of cutout
    
    Ellipse(float radiusX = 100, float radiusY = 50, 
            SDL_Color fillColor = {0, 0, 0, 255},
            SDL_Color strokeColor = {0, 0, 0, 255}, float strokeWidth = 1.0f,
            float cutoutY = 0, float cutoutHeight = 0)
        : radiusX(radiusX), radiusY(radiusY), cutoutY(cutoutY), cutoutHeight(cutoutHeight) {
        this->fillColor = fillColor;
        this->strokeColor = strokeColor;
        this->strokeWidth = strokeWidth;
    }
    
    void render(SDL_Renderer* renderer, const Vec3& facePosition, const Vec3& faceRotation) override {
        SDL_SetRenderDrawColor(renderer, fillColor.r, fillColor.g, fillColor.b, fillColor.a);
        
        // Calculate effective radii based on perspective
        float effectiveRadiusX = radiusX * (1.0f / (1.0f + (localPosition.z + facePosition.z) * 0.001f));
        float effectiveRadiusY = radiusY * (1.0f / (1.0f + (localPosition.z + facePosition.z) * 0.001f));
        
        // Draw filled ellipse by projecting each point in 3D space
        // Use step size of 2 to reduce number of points
        for (int y = -effectiveRadiusY; y <= effectiveRadiusY; y += 2) {
            for (int x = -effectiveRadiusX; x <= effectiveRadiusX; x += 2) {
                // Calculate if point is in the main ellipse
                float ellipseValue = (x*x)/(float)(effectiveRadiusX*effectiveRadiusX) + (y*y)/(float)(effectiveRadiusY*effectiveRadiusY);
                
                // Calculate if point is in the cutout ellipse
                float cutoutValue = 0.0f;
                if (cutoutHeight > 0) {
                    float cutoutRadiusY = cutoutHeight / 2.0f;
                    float cutoutRadiusX = effectiveRadiusX * (cutoutRadiusY / effectiveRadiusY);
                    cutoutValue = (x*x)/(float)(cutoutRadiusX*cutoutRadiusX) + 
                                 ((y-cutoutY)*(y-cutoutY))/(float)(cutoutRadiusY*cutoutRadiusY);
                }
                
                // Draw point if it's in the main ellipse but not in the cutout
                if (ellipseValue <= 1.0f && (cutoutHeight == 0 || cutoutValue > 1.0f)) {
                    // Create point in local space
                    Vec3 point(x, y, 0);
                    // Add shape position and face position
                    point = point + localPosition + facePosition;
                    // Project to screen
                    SDL_Point screenPoint = Projection::project(point, faceRotation);
                    drawLargeDot(renderer, screenPoint.x, screenPoint.y);
                }
            }
        }
    }
};

// Vector face class to manage all face elements
class VectorFace {
private:
    std::vector<VectorShape*> shapes;
    Vec3 position;
    Vec3 rotation;
    
public:
    VectorFace() : position(0, 0, 0), rotation(0, 0, 0) {}
    ~VectorFace() {
        for (auto shape : shapes) {
            delete shape;
        }
    }
    
    void addShape(VectorShape* shape) {
        shapes.push_back(shape);
    }
    
    void setRotation(const Vec3& rot) {
        rotation = rot;
    }
    
    void render(SDL_Renderer* renderer) {
        for (auto shape : shapes) {
            if (shape->visible) {
                shape->render(renderer, position, rotation);
            }
        }
    }
};

// Vector renderer manager
class VectorRenderer {
private:
    VectorFace face;
    
public:
    void addShape(VectorShape* shape) {
        face.addShape(shape);
    }
    
    void render(SDL_Renderer* renderer) {
        face.render(renderer);
    }
    
    void setFaceRotation(const Vec3& rotation) {
        face.setRotation(rotation);
    }
};

// Shared renderer instance, defined in main.cpp
extern VectorRenderer vectorRenderer;
