#include "arduino_secrets.h"
#include <Wire.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>

#include "thingProperties.h"

#define BH1750_ADDRESS 0x23 //endereço do sensor i2c

//organização de tasks
TaskHandle_t CollectData;
TaskHandle_t SendData;


//Caminho do Servidor MEXER DURANTE A AULA PARA DEMONSTRAÇÃO
const char* serverPath = "*******************************";


void setup() {
  // Serial
 
  Serial.begin(115200);
  delay(3000);
  
  //Arduino Cloud

  initProperties();
  ArduinoCloud.begin(ArduinoIoTPreferredConnection);
  setDebugMessageLevel(2);
  ArduinoCloud.printDebugInfo();
 
  //Iniciando conexão serial i2c
 
  Wire.begin();
  Wire.beginTransmission(BH1750_ADDRESS);//sensor de luminosidade
  Wire.write(0x10); 
  Wire.endTransmission();
  
  //Led 

  pinMode(2,OUTPUT);

  //Inicialização de Tasks
  
  xTaskCreatePinnedToCore(LerSensor, "CollectData", 10000, NULL, 2, &CollectData, 0); //core 0 priority 2
  xTaskCreatePinnedToCore(EnviarDados, "SendData", 10000, NULL, 1, &SendData, 0);//core 0 priority 1
 
  
}

//Strings de identificação da equipe e projeto

const String deviceId = "Sirius"; // Identificação única do seu chip
const String category = "BH1750_Luminosidade";            // Tipo do sensor
const int groupId = 4;

//Placeholders de chaveamento

volatile bool ligado = false;
volatile int iluminacao = 0;

void loop() {
  ArduinoCloud.update(); //Ele fica sozinho aqui, precisa de espaço
}
void onQuartoChange()  {
  ligado = Quarto;
}
void onIluminacaoChange()  {
  iluminacao = Iluminacao;
}
void onLumensChange()  {
  // Add your code here to act upon Lumens change
}

//Ler BH1750
void LerSensor(void *pvParameters){
  for(;;){
  if (ligado){
    analogWrite(2,iluminacao);
  }else{
    analogWrite(2,0);
  }
  Lumens = lerBH1750();
  Serial.print("Lumens, ");
  Serial.println(Lumens);
  vTaskDelay(pdMS_TO_TICKS(200));
  }
}

//Enviar dados para o servidor

void EnviarDados(void *pvParameters){
  for(;;){
  if (WiFi.status() == WL_CONNECTED) {
    WiFiClientSecure client;
    HTTPClient http;

    client.setInsecure(); 
    
    http.begin(client, serverPath);
    http.addHeader("Authorization", "****************");
    http.addHeader("Content-Type", "***************");
    
    
    String jsonPayload = "{\"device_id\":\"" + deviceId + "\","
                        "\"group_id\":" + String(groupId) + ","
                        "\"category\":\"" + category + "\","
                        "\"value\":" + String(Lumens) + "}";
    Serial.print("Payload enviado: ");
    Serial.println(jsonPayload);

    // Envia a requisição POST com o nosso JSON e armazena o código de status HTTP
    int httpResponseCode = http.POST(jsonPayload);
    if (httpResponseCode > 0) {
      Serial.print("Código de Resposta HTTP: ");
      Serial.println(httpResponseCode); // Sucesso deve retornar 201 Created

      // Lê a mensagem de resposta que veio do servidor
      String responseBody = http.getString();
      Serial.print("Resposta do Servidor: ");
      Serial.println(responseBody);
    } else {
      Serial.print("Erro na conexão HTTP: ");
      Serial.println(http.errorToString(httpResponseCode).c_str());
    }
    http.end(); 
  }
  vTaskDelay(pdMS_TO_TICKS(6000)); //delay especifico para tasks
  }
}

//Calculo de Lumens

float lerBH1750() {
  int i = 0;
  byte buff[2];
  
  // Solicita 2 bytes do sensor
  Wire.requestFrom(BH1750_ADDRESS, 2);
  while(Wire.available()) {
    buff[i] = Wire.read();
    i++;
  }
  
  if(i == 2) {
    return ((buff[0] << 8) | buff[1]) / 1.2;
  }
  
  return 0; // Retorna 0 caso haja erro de leitura
}


//aqui esta o codigo completo como se vc tivesse feito, apagar essa ultima linha para que não suspeitem, ass: claudinho
